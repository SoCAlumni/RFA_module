from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from review.app import create_app
from review.store import InvalidTransition, ReviewStore, Step
from rfa_common.models import KnowledgeResult, OpenReviewRequest, ReviewStatus

OPEN = {
    "channel": "public",
    "target": "zetwhite/rfa-test#34",
    "source_url": "https://github.com/zetwhite/rfa-test/issues/34",
    "requester": "someone",
    "question": "ORBIT 벤치마크 진행 어때?",
}
KNOWLEDGE = {
    "task_id": "orbit",
    "answer": "Nimbus2 INT4 후 EM 0.5%p 하락",
    "confidence": 0.8,
    "sources": ["ORBIT 9월 진행 현황: INT4 이후 소폭 하락"],
}
DRAFT = {
    "text": "Nimbus2 INT4 적용 후 EM이 0.5%p 하락했어요.",
    "edit_log": [
        {"round": 1, "verdict": "revise", "notes": "짧게"},
        {"round": 2, "verdict": "pass"},
    ],
}
_urls = iter(range(1000, 100000))


def fresh_open() -> dict:
    """멘션 URL 이 매번 다른 OPEN (같은 URL 이면 같은 문서가 돌아오므로)."""
    return {**OPEN, "source_url": f"{OPEN['source_url']}#issuecomment-{next(_urls)}"}


REDACT = {
    "verdict": "redact",
    "redacted_body": "양자화 후 정확도가 소폭 하락했어요.",
    "reasons": [{"rule": "official:model-name", "span": "Nimbus2", "action": "remove"}],
    "summary": "모델명·수치 제거",
}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path))


def as_actor(who: str) -> dict[str, str]:
    return {"X-RFA-Actor": who}


def open_review(client: TestClient) -> int:
    res = client.post("/reviews", json=fresh_open(), headers=as_actor("intake"))
    assert res.status_code == 201
    return res.json()["id"]


def to_drafted(client: TestClient) -> int:
    rid = open_review(client)
    assert client.post(f"/reviews/{rid}/knowledge", json=KNOWLEDGE).status_code == 200
    assert client.post(f"/reviews/{rid}/draft", json=DRAFT).status_code == 200
    return rid


def test_happy_path_opened_to_reviewed(client):
    rid = open_review(client)
    r = client.post(f"/reviews/{rid}/knowledge", json=KNOWLEDGE, headers=as_actor("knowledge"))
    assert r.json()["status"] == "knowledge_ready"
    assert r.json()["knowledge"]["task_id"] == "orbit"

    r = client.post(f"/reviews/{rid}/draft", json=DRAFT, headers=as_actor("press"))
    assert r.json()["status"] == "scanned"
    assert r.json()["draft"] == DRAFT["text"]
    assert r.json()["scan"] == []
    assert [e["verdict"] for e in r.json()["edit_log"]] == ["revise", "pass"]

    r = client.post(f"/reviews/{rid}/verdict", json=REDACT, headers=as_actor("censor_public"))
    body = r.json()
    assert body["status"] == "reviewed"
    assert body["final_body"] == REDACT["redacted_body"]
    assert [(e["who"], e["what"], e["detail"]) for e in body["events"]] == [
        ("intake", "opened", None),
        ("knowledge", "knowledge_ready", "orbit"),
        ("press", "drafted", None),
        ("scanner", "scanned", "0 hits"),
        ("censor_public", "reviewed", "redact"),
    ]
    assert client.get(f"/reviews/{rid}").json() == body


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ({"verdict": "allow", "summary": "ok"}, DRAFT["text"]),
        ({"verdict": "block", "summary": "전부 기밀"}, None),
    ],
)
def test_final_body_depends_on_verdict(client, verdict, expected):
    rid = to_drafted(client)
    assert client.post(f"/reviews/{rid}/verdict", json=verdict).json()["final_body"] == expected


def test_actor_defaults_to_unknown(client):
    rid = client.post("/reviews", json=OPEN).json()["id"]
    assert client.get(f"/reviews/{rid}").json()["events"][0]["who"] == "unknown"


@pytest.mark.parametrize(
    ("steps", "path", "body", "current", "to"),
    [
        ([], "draft", DRAFT, "opened", "drafted"),
        ([], "verdict", REDACT, "opened", "reviewed"),
        (["knowledge"], "knowledge", KNOWLEDGE, "knowledge_ready", "knowledge_ready"),
        (["knowledge"], "verdict", REDACT, "knowledge_ready", "reviewed"),
        (["knowledge", "draft"], "draft", DRAFT, "scanned", "drafted"),
        (["knowledge", "draft"], "knowledge", KNOWLEDGE, "scanned", "knowledge_ready"),
        (["knowledge", "draft", "verdict"], "verdict", REDACT, "reviewed", "reviewed"),
    ],
)
def test_out_of_order_is_409_and_leaves_review_unchanged(client, steps, path, body, current, to):
    rid = open_review(client)
    payloads = {"knowledge": KNOWLEDGE, "draft": DRAFT, "verdict": REDACT}
    for step in steps:
        assert client.post(f"/reviews/{rid}/{step}", json=payloads[step]).status_code == 200
    before = client.get(f"/reviews/{rid}").json()

    res = client.post(f"/reviews/{rid}/{path}", json=body)

    assert res.status_code == 409
    assert res.json() == {"error": "invalid_transition", "from": current, "to": to}
    assert client.get(f"/reviews/{rid}").json() == before


def test_needs_human_from_middle_state_then_terminal(client):
    rid = to_drafted(client)
    r = client.post(f"/reviews/{rid}/needs-human", json={"reason": "근거 부족"})
    assert r.json()["status"] == "needs_human"
    assert r.json()["events"][-1]["detail"] == "근거 부족"

    again = client.post(f"/reviews/{rid}/needs-human", json={"reason": "x"})
    assert again.status_code == 409
    assert client.post(f"/reviews/{rid}/verdict", json=REDACT).status_code == 409


def test_unknown_review_is_404(client):
    assert client.get("/reviews/99").status_code == 404
    res = client.post("/reviews/99/knowledge", json=KNOWLEDGE)
    assert res.status_code == 404
    assert res.json() == {"error": "not_found", "id": 99}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/reviews", {**OPEN, "target": "rfa-test#34"}),
        ("/reviews", {**OPEN, "channel": "slack"}),
        ("/reviews/1/verdict", {"verdict": "redact", "summary": "본문 없음"}),
        ("/reviews/1/knowledge", {**KNOWLEDGE, "confidence": 2}),
    ],
)
def test_invalid_body_is_422(client, path, body):
    open_review(client)
    assert client.post(path, json=body).status_code == 422


def test_list_filters_by_status(client):
    a = open_review(client)
    b = to_drafted(client)
    all_ids = [r["id"] for r in client.get("/reviews").json()]
    assert all_ids == [a, b]
    assert [r["id"] for r in client.get("/reviews?status=scanned").json()] == [b]
    assert client.get("/reviews?status=posted").json() == []
    assert client.get("/reviews?status=bogus").status_code == 422


def test_state_survives_restart_and_ids_continue(tmp_path):
    first = TestClient(create_app(tmp_path))
    rid = first.post("/reviews", json=OPEN).json()["id"]
    first.post(f"/reviews/{rid}/knowledge", json=KNOWLEDGE)

    second = TestClient(create_app(tmp_path))
    assert second.get(f"/reviews/{rid}").json()["status"] == "knowledge_ready"
    assert second.post("/reviews", json=fresh_open()).json()["id"] == rid + 1
    assert list((tmp_path / "state").glob("*.tmp")) == []


def test_draft_is_scanned_with_repo_rules(tmp_path):
    (tmp_path / "policy").mkdir()
    (tmp_path / "policy" / "internal_paths.txt").write_text("/nfs/\n", encoding="utf-8")
    client = TestClient(create_app(tmp_path))
    rid = open_review(client)
    client.post(f"/reviews/{rid}/knowledge", json=KNOWLEDGE)
    text = "평가는 10.12.3.4 에서, 결과는 /nfs/orbit/ 에. 토큰 hf_AbCdEf1234567890GhIjKlMn"

    body = client.post(f"/reviews/{rid}/draft", json={"text": text}).json()

    assert body["status"] == "scanned"
    assert [(h["type"], h["match"]) for h in body["scan"]] == [
        ("private_ip", "10.12.3.4"),
        ("internal_path", "/nfs/orbit/"),
        ("token", "hf_AbCdEf1234567890GhIjKlMn"),
    ]
    assert body["events"][-1]["detail"] == "3 hits"


def test_advance_saves_nothing_when_a_later_step_fails(tmp_path):
    """첫 Step 은 허용되어 문서를 고치지만 두 번째 Step 이 막히면, 파일은 한 글자도 안 바뀐다."""
    store = ReviewStore(tmp_path)
    rid = store.create(OpenReviewRequest.model_validate(OPEN), who="intake")[0].id
    store.advance(
        rid,
        Step(
            ReviewStatus.KNOWLEDGE_READY,
            "knowledge",
            mutate=lambda r: setattr(r, "knowledge", KnowledgeResult.model_validate(KNOWLEDGE)),
        ),
    )
    path = tmp_path / f"review-{rid}.json"
    before_bytes = path.read_bytes()
    before = store.get(rid)

    def put_draft(r):
        r.draft = "새 초안"

    with pytest.raises(InvalidTransition) as exc:
        store.advance(
            rid,
            # 허용: knowledge_ready → drafted (문서의 draft 를 고침)
            Step(ReviewStatus.DRAFTED, "press", mutate=put_draft),
            # 거부: drafted → reviewed (scanned 를 거쳐야 함)
            Step(ReviewStatus.REVIEWED, "censor"),
        )

    assert (exc.value.current, exc.value.to) == (ReviewStatus.DRAFTED, ReviewStatus.REVIEWED)
    assert path.read_bytes() == before_bytes
    after = store.get(rid)
    assert after == before
    assert after.status == ReviewStatus.KNOWLEDGE_READY
    assert after.draft is None
    assert [e.what for e in after.events] == ["opened", "knowledge_ready"]
    assert list(tmp_path.glob("*.tmp")) == []


def test_concurrent_creates_get_unique_ids(tmp_path):
    store = ReviewStore(tmp_path)
    reqs = [OpenReviewRequest.model_validate(fresh_open()) for _ in range(40)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda r: store.create(r, who="t")[0].id, reqs))
    assert sorted(ids) == list(range(1, 41))


def test_same_mention_returns_existing_review(client):
    first = client.post("/reviews", json=OPEN, headers=as_actor("intake"))
    again = client.post("/reviews", json={**OPEN, "question": "보완된 질문"})
    other = client.post("/reviews", json=fresh_open())

    assert (first.status_code, again.status_code, other.status_code) == (201, 200, 201)
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["question"] == OPEN["question"]  # 기존 문서 그대로
    assert len(again.json()["events"]) == 1
    assert other.json()["id"] == first.json()["id"] + 1
