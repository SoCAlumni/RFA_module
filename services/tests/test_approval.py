import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from review.app import create_app
from review.clearance import verify
from review.publisher import MockPublisher
from svc_support import TEST_CLEARANCE_KEY
from test_review_core import DRAFT, KNOWLEDGE, OPEN, REDACT, fresh_open

LOOPBACK = ("127.0.0.1", 40000)


@pytest.fixture
def publisher() -> MockPublisher:
    return MockPublisher(clearance_key=TEST_CLEARANCE_KEY)


@pytest.fixture
def setup(tmp_path: Path, publisher: MockPublisher) -> tuple[TestClient, TestClient]:
    """(사람: loopback, 에이전트: 비-loopback) 클라이언트 쌍."""
    app = create_app(tmp_path, publisher=publisher)
    return TestClient(app, client=LOOPBACK), TestClient(app, client=("172.17.0.5", 40000))


def to_reviewed(client: TestClient, verdict: dict = REDACT, draft: dict = DRAFT) -> int:
    rid = client.post("/reviews", json=fresh_open()).json()["id"]
    assert client.post(f"/reviews/{rid}/knowledge", json=KNOWLEDGE).status_code == 200
    assert client.post(f"/reviews/{rid}/draft", json=draft).status_code == 200
    assert client.post(f"/reviews/{rid}/verdict", json=verdict).status_code == 200
    return rid


def feedback_lines(tmp_path: Path) -> list[dict]:
    path = tmp_path / "policy" / "feedback.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_app_requires_clearance_key(tmp_path, monkeypatch):
    monkeypatch.delenv("RFA_CLEARANCE_KEY")
    with pytest.raises(RuntimeError, match="RFA_CLEARANCE_KEY"):
        create_app(tmp_path)


def test_approve_publishes_redacted_body_and_records(setup, publisher, tmp_path):
    human, _ = setup
    rid = to_reviewed(human)

    res = human.post(f"/reviews/{rid}/approve")

    assert res.status_code == 200
    assert res.json() == {"status": "posted", "posted_url": f"mock://{OPEN['target']}/comment-1"}
    ((target, body, token),) = publisher.published
    assert (target, body) == (OPEN["target"], REDACT["redacted_body"])
    assert verify(TEST_CLEARANCE_KEY, token, target, body)["review_id"] == rid

    doc = human.get(f"/reviews/{rid}").json()
    assert doc["status"] == "posted"
    assert doc["decision"]["by"] == "human" and doc["decision"]["reason"] is None
    assert [(e["who"], e["what"]) for e in doc["events"][-2:]] == [
        ("human", "approved"),
        ("publisher", "posted"),
    ]
    # approve + verdict=redact 는 feedback 에 남는다
    assert [(f["decision"], f["review_id"]) for f in feedback_lines(tmp_path)] == [("approve", rid)]


def test_approve_allow_verdict_publishes_draft_without_feedback(setup, publisher, tmp_path):
    human, _ = setup
    rid = to_reviewed(human, verdict={"verdict": "allow", "summary": "ok"})
    assert human.post(f"/reviews/{rid}/approve").status_code == 200
    assert publisher.published[0][1] == DRAFT["text"]
    assert feedback_lines(tmp_path) == []


def test_reject_records_reason_as_feedback(setup, tmp_path):
    human, _ = setup
    rid = to_reviewed(human)

    res = human.post(f"/reviews/{rid}/reject", json={"reason": "QAT 검토 사실도 비공개"})

    assert res.status_code == 200
    doc = res.json()
    assert doc["status"] == "rejected"
    assert doc["decision"]["reason"] == "QAT 검토 사실도 비공개"
    (line,) = feedback_lines(tmp_path)
    assert line["decision"] == "reject"
    assert line["reason"] == "QAT 검토 사실도 비공개"
    assert line["scope"] == "public"
    assert line["draft_excerpt"].startswith(REDACT["redacted_body"][:20])


def test_non_loopback_cannot_approve_or_reject(setup):
    human, agent = setup
    rid = to_reviewed(human)

    assert agent.post(f"/reviews/{rid}/approve").status_code == 403
    res = agent.post(f"/reviews/{rid}/reject", json={"reason": "x"})
    assert res.status_code == 403
    assert res.json() == {"error": "loopback_only"}
    assert human.get(f"/reviews/{rid}").json()["status"] == "reviewed"


def test_approve_before_reviewed_is_409(setup):
    human, _ = setup
    rid = human.post("/reviews", json=OPEN).json()["id"]
    res = human.post(f"/reviews/{rid}/approve")
    assert res.status_code == 409
    assert res.json() == {"error": "invalid_transition", "from": "opened", "to": "approved"}


def test_approve_after_posted_is_409(setup):
    human, _ = setup
    rid = to_reviewed(human)
    assert human.post(f"/reviews/{rid}/approve").status_code == 200
    assert human.post(f"/reviews/{rid}/approve").status_code == 409
    assert human.post(f"/reviews/{rid}/reject", json={"reason": "x"}).status_code == 409


def test_block_verdict_cannot_be_approved(setup, publisher):
    human, _ = setup
    rid = to_reviewed(human, verdict={"verdict": "block", "summary": "전부 기밀"})
    res = human.post(f"/reviews/{rid}/approve")
    assert res.status_code == 409
    assert res.json() == {"error": "verdict_is_block"}
    assert publisher.published == []
    assert human.get(f"/reviews/{rid}").json()["status"] == "reviewed"


def test_token_left_in_final_body_blocks_approve(setup, publisher):
    human, _ = setup
    leaky = "토큰 hf_AbCdEf1234567890GhIjKlMn 포함 본문"
    rid = to_reviewed(
        human,
        draft={"text": leaky, "edit_log": []},
        verdict={"verdict": "allow", "summary": "censor 가 놓침"},
    )
    res = human.post(f"/reviews/{rid}/approve")
    assert res.status_code == 409
    assert res.json() == {"error": "secrets_in_final_body"}
    assert publisher.published == []


def test_publisher_failure_leaves_review_approved(tmp_path):
    failing = MockPublisher(clearance_key=TEST_CLEARANCE_KEY, fail=True)
    human = TestClient(create_app(tmp_path, publisher=failing), client=LOOPBACK)
    rid = to_reviewed(human)

    res = human.post(f"/reviews/{rid}/approve")

    assert res.status_code == 502
    assert res.json()["error"] == "publish_failed"
    doc = human.get(f"/reviews/{rid}").json()
    assert doc["status"] == "approved"  # 승인 기록은 남고, 게시 재시도는 이후 단계의 몫
    assert failing.published == []


def test_publisher_rejects_wrong_key_token(tmp_path):
    wrong_key = MockPublisher(clearance_key="different-key")
    human = TestClient(create_app(tmp_path, publisher=wrong_key), client=LOOPBACK)
    rid = to_reviewed(human)
    res = human.post(f"/reviews/{rid}/approve")
    assert res.status_code == 502
    assert "clearance rejected" in res.json()["detail"]
    assert wrong_key.published == []
