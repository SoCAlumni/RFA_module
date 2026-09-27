import threading
import time
from pathlib import Path

import pytest
import yaml
from approvals.app import create_app
from approvals.publisher import LivePublisher, MockPublisher, PublishError, make_publisher
from approvals.store import MAX_ROUNDS
from channels.base import ChannelError
from channels.github import (
    BOT_MARKER,
    GithubChannel,
    GithubClient,
    GithubConfig,
    MentionTracker,
    NotificationTracker,
)
from fake_github import REPO, FakeGithub
from fastapi.testclient import TestClient

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "approvals.openapi.yaml"

GITHUB = {
    "channel": "github",
    "audience": "public",
    "target": "team/rfa-test#34",
    "source_url": "https://github.com/team/rfa-test/issues/34#issuecomment-1",
    "requester": "outside-dev",
    "question": "ORBIT 벤치마크 결과가 언제쯤 공개되나요?",
    "context": [{"author": "outside-dev", "text": "언제쯤?", "at": "2026-09-27T08:40:00Z"}],
    "task": {"id": "orbit", "name": "ORBIT 벤치마크"},
    "knowledge": "ORBIT 는 INT4 이후 소폭 하락, QAT 보완 중. 11/3 릴리즈.",
    "draft": "안녕하세요. 11/3 릴리즈에 맞춰 QAT 를 적용 중입니다.",
}
SLACK = {
    **GITHUB,
    "channel": "slack",
    "audience": "company",
    "target": "C0123ABC/1727000000.000100",
    "source_url": "https://slack.com/archives/C0123ABC/p1727000000000100",
    "requester": "product-team",
    "question": "PRISM 설계 문서 어디 있나요?",
    "task": None,
    "knowledge": "",
    "refusal": "관련 업무를 찾지 못했습니다",
    "draft": "확인 후 알려드릴게요.",
}
REVISED = {
    "task": {"id": "orbit", "name": "ORBIT 벤치마크"},
    "knowledge": "ORBIT 는 INT4 이후 소폭 하락, QAT 보완 중.",
    "refusal": None,
    "draft": "안녕하세요. 양자화 이후 정확도 보완 작업을 진행 중입니다.",
}


@pytest.fixture
def publisher() -> MockPublisher:
    return MockPublisher()


@pytest.fixture
def client(tmp_path, publisher) -> TestClient:
    return TestClient(create_app(tmp_path, publisher=publisher, env={}))


def create(client: TestClient, body: dict = GITHUB) -> dict:
    res = client.post("/approvals", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def reject(client: TestClient, aid: int, reason: str = "릴리즈 날짜가 들어가 있음") -> dict:
    res = client.post(f"/approvals/{aid}/reject", json={"reason": reason})
    assert res.status_code == 200, res.text
    return res.json()


def revise(client: TestClient, aid: int) -> dict:
    res = client.post(f"/approvals/{aid}/revise", json=REVISED)
    assert res.status_code == 200, res.text
    return res.json()


def whats(approval: dict) -> list[tuple[str, str]]:
    return [(e["who"], e["what"]) for e in approval["events"]]


# ---- 생성 --------------------------------------------------------------------


def test_create_opens_pending_round_one(client):
    a = create(client)
    assert (a["id"], a["status"], a["round"]) == (1, "pending", 1)
    assert a["rejections"] == [] and a["posted_url"] is None
    assert a["created_at"] == a["updated_at"]
    assert whats(a) == [("desk", "pending")]
    assert client.get("/approvals/1").json() == a


def test_create_same_source_url_returns_existing(client):
    first = create(client)
    again = client.post("/approvals", json={**GITHUB, "draft": "다른 초안"})
    assert again.status_code == 200
    assert again.json() == first


def test_create_rejects_target_of_wrong_channel(client):
    res = client.post("/approvals", json={**SLACK, "target": "team/rfa-test#34"})
    assert res.status_code == 422


def test_get_unknown_is_404(client):
    res = client.get("/approvals/99")
    assert res.status_code == 404
    assert res.json()["error"] == "not_found"


def test_ids_persist_across_restart(tmp_path):
    create(TestClient(create_app(tmp_path, publisher=MockPublisher(), env={})))
    reopened = TestClient(create_app(tmp_path, publisher=MockPublisher(), env={}))
    assert reopened.get("/approvals/1").json()["question"] == GITHUB["question"]
    assert create(reopened, SLACK)["id"] == 2


# ---- 승인 --------------------------------------------------------------------


def test_approve_publishes_draft_to_its_channel(client, publisher):
    create(client)
    res = client.post("/approvals/1/approve")
    assert res.status_code == 200
    a = res.json()
    assert a["status"] == "posted"
    assert a["posted_url"] == "mock://github/team/rfa-test#34/1"
    assert publisher.published == [("github", GITHUB["target"], GITHUB["draft"])]
    assert whats(a) == [("desk", "pending"), ("human", "approved"), ("publisher", "posted")]


def test_approve_twice_does_not_post_twice(client, publisher):
    create(client)
    client.post("/approvals/1/approve")
    res = client.post("/approvals/1/approve")
    assert res.status_code == 409
    assert res.json()["error"] == "invalid_transition"
    assert len(publisher.published) == 1


def test_second_click_during_publish_does_not_post_twice(tmp_path):
    """첫 승인이 게시하는 도중(상태 approved)에 두 번째 클릭이 오면, '게시 재시도'로 착각해
    한 번 더 게시하면 안 된다. 두 번째 요청은 첫 게시가 끝날 때까지 기다렸다가 409."""
    publishing = threading.Event()

    class SlowPublisher(MockPublisher):
        def publish(self, channel, target, body):
            publishing.set()
            time.sleep(0.2)
            return super().publish(channel, target, body)

    publisher = SlowPublisher()
    client = TestClient(create_app(tmp_path, publisher=publisher, env={}))
    create(client)
    codes: list[int] = []

    def click() -> None:
        codes.append(client.post("/approvals/1/approve").status_code)

    first = threading.Thread(target=click)
    first.start()
    assert publishing.wait(timeout=2)  # 첫 요청이 approved 로 옮기고 게시 중
    click()
    first.join()
    assert sorted(codes) == [200, 409]
    assert len(publisher.published) == 1


def test_publish_failure_keeps_approved_and_retry_posts(client, publisher):
    create(client)
    publisher.fail = True
    res = client.post("/approvals/1/approve")
    assert res.status_code == 502
    assert res.json() == {"error": "publish_failed", "detail": "mock channel down"}
    assert client.get("/approvals/1").json()["status"] == "approved"

    publisher.fail = False
    a = client.post("/approvals/1/approve").json()
    assert a["status"] == "posted"
    assert len(publisher.published) == 1
    assert [w for _, w in whats(a)].count("approved") == 1


def test_cannot_approve_rejected(client, publisher):
    create(client)
    reject(client, 1)
    assert client.post("/approvals/1/approve").status_code == 409
    assert publisher.published == []


def test_approve_unknown_is_404(client):
    assert client.post("/approvals/9/approve").status_code == 404


# ---- 거절과 재작성 --------------------------------------------------------------


def test_reject_records_draft_and_reason(client):
    create(client)
    a = reject(client, 1)
    assert a["status"] == "rejected"
    ((r,),) = [a["rejections"]]
    assert (r["draft"], r["reason"]) == (GITHUB["draft"], "릴리즈 날짜가 들어가 있음")
    assert a["events"][-1] | {"at": None} == {
        "at": None,
        "who": "human",
        "what": "rejected",
        "detail": "릴리즈 날짜가 들어가 있음",
    }


def test_reject_requires_reason(client):
    create(client)
    assert client.post("/approvals/1/reject", json={"reason": ""}).status_code == 422
    assert client.post("/approvals/1/reject", json={}).status_code == 422


def test_reject_only_pending(client):
    create(client)
    client.post("/approvals/1/approve")
    assert client.post("/approvals/1/reject", json={"reason": "x"}).status_code == 409


def test_revise_after_reject_bumps_round_and_replaces_draft(client):
    create(client)
    reject(client, 1)
    a = revise(client, 1)
    assert (a["status"], a["round"]) == ("pending", 2)
    assert (a["draft"], a["knowledge"]) == (REVISED["draft"], REVISED["knowledge"])
    assert a["rejections"][0]["draft"] == GITHUB["draft"]  # 이전 초안은 이력에 남는다
    assert a["events"][-1]["detail"] == "round 2"


def test_revise_only_rejected(client):
    create(client)
    res = client.post("/approvals/1/revise", json=REVISED)
    assert res.status_code == 409


def test_third_rejection_closes(client):
    create(client)
    for _ in range(MAX_ROUNDS - 1):
        reject(client, 1)
        revise(client, 1)
    a = reject(client, 1, "여전히 기밀")
    assert (a["status"], a["round"]) == ("closed", MAX_ROUNDS)
    assert len(a["rejections"]) == MAX_ROUNDS
    assert whats(a)[-2:] == [("human", "rejected"), ("system", "closed")]
    assert client.post("/approvals/1/revise", json=REVISED).status_code == 409


# ---- 목록과 집계 ----------------------------------------------------------------


def test_list_newest_update_first_and_filters(client):
    create(client)  # 1 github orbit
    create(client, SLACK)  # 2 slack, task 없음
    reject(client, 1)  # 1 이 더 최근에 바뀜

    def ids(query: str = "") -> list[int]:
        return [a["id"] for a in client.get(f"/approvals{query}").json()]

    assert ids() == [1, 2]
    assert ids("?status=pending") == [2]
    assert ids("?channel=github") == [1]
    assert ids("?task=orbit") == [1]
    assert ids("?task=orbit&status=pending") == []
    assert client.get("/approvals?status=nope").status_code == 422


def test_summary_counts_by_channel_and_task(client):
    create(client)
    create(client, SLACK)
    create(client, {**SLACK, "source_url": "https://slack.com/archives/C0123ABC/p2"})
    reject(client, 2)
    assert client.get("/approvals/summary").json() == {
        "by_channel": {"github": {"pending": 1}, "slack": {"pending": 1, "rejected": 1}},
        "by_task": {"orbit": {"pending": 1}, "(none)": {"pending": 1, "rejected": 1}},
    }


def test_summary_empty(client):
    assert client.get("/approvals/summary").json() == {"by_channel": {}, "by_task": {}}


# ---- CORS, 참조 웹, 게시자 선택 ---------------------------------------------------


def test_cors_allows_any_origin_by_default(client):
    res = client.options(
        "/approvals/1/approve",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "*"


def test_cors_origins_from_env(tmp_path):
    env = {"RFA_CORS_ORIGINS": "http://localhost:5173, http://127.0.0.1:3000"}
    client = TestClient(create_app(tmp_path, publisher=MockPublisher(), env=env))
    ok = client.get("/approvals", headers={"Origin": "http://127.0.0.1:3000"})
    assert ok.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    other = client.get("/approvals", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_reference_page_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "/approvals/summary" in res.text


def test_make_publisher(tmp_path):
    assert isinstance(make_publisher({}), MockPublisher)
    live = make_publisher(
        {
            "RFA_PUBLISHER": "live",
            "RFA_CHANNELS": "github",
            "RFA_DATA_DIR": str(tmp_path),
            "GITHUB_TOKEN": "t",
            "RFA_GITHUB_LOGIN": "zetwhite",
            "RFA_GITHUB_REPOS": "a/b",
        }
    )
    assert isinstance(live, LivePublisher) and list(live.channels) == ["github"]
    with pytest.raises(RuntimeError, match="unknown RFA_PUBLISHER"):
        make_publisher({"RFA_PUBLISHER": "github"})  # v1 값
    with pytest.raises(PublishError):
        MockPublisher(fail=True).publish("github", "a/b#1", "x")


class RecordingChannel:
    kind = "github"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.posted: list[tuple[str, str]] = []

    def poll(self):
        return []

    def post(self, target: str, body: str) -> str:
        if self.error:
            raise self.error
        self.posted.append((target, body))
        return f"https://github.com/{target}#posted"


def test_live_publisher_posts_to_the_approvals_channel():
    ch = RecordingChannel()
    url = LivePublisher({"github": ch}).publish("github", "a/b#1", "답")
    assert (url, ch.posted) == ("https://github.com/a/b#1#posted", [("a/b#1", "답")])


def test_live_publisher_errors_become_publish_error():
    with pytest.raises(PublishError, match="slack 채널이 켜져 있지 않음"):
        LivePublisher({"github": RecordingChannel()}).publish("slack", "C1/1.2", "x")
    broken = RecordingChannel(ChannelError("POST comment a/b#1: 403 forbidden"))
    with pytest.raises(PublishError, match="403"):
        LivePublisher({"github": broken}).publish("github", "a/b#1", "x")


def test_approve_posts_to_github_through_live_publisher(tmp_path):
    """결재 서버 → LivePublisher → GithubChannel → (가짜) GitHub 댓글. 실패하면 502 후 재시도."""
    fake = FakeGithub(fail_post=502)
    client = GithubClient("t", transport=fake.transport())
    config = GithubConfig(token="t", repos=(REPO,))
    channel = GithubChannel(
        client,
        config,
        NotificationTracker(tmp_path / "noti_seen.json"),
        MentionTracker(tmp_path / "seen.json"),
    )
    app = TestClient(create_app(tmp_path, publisher=LivePublisher({"github": channel}), env={}))
    create(app, {**GITHUB, "target": f"{REPO}#34"})  # 가짜 GitHub 는 REPO 하나만 흉내 낸다

    failed = app.post("/approvals/1/approve")
    assert failed.status_code == 502 and "502" in failed.json()["detail"]
    assert app.get("/approvals/1").json()["status"] == "approved"

    fake.fail_post = None
    a = app.post("/approvals/1/approve").json()
    assert a["status"] == "posted"
    assert a["posted_url"] == f"https://github.com/{REPO}/issues/34#issuecomment-999"
    assert fake.posted == [(f"{REPO}#34", f"{GITHUB['draft']}\n\n{BOT_MARKER}")]


# ---- 계약 대조 -----------------------------------------------------------------


def _routes(spec: dict) -> set[tuple[str, str, str]]:
    """(경로, 메서드, 태그). FastAPI 의 {approval_id} 와 yaml 의 {id} 를 맞춘다."""
    return {
        (path.replace("{approval_id}", "{id}"), method, op["tags"][0])
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if method in {"get", "post"}
    }


def test_app_routes_match_contract(client):
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    assert _routes(client.app.openapi()) == _routes(contract)
