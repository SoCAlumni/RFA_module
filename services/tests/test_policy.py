import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from review.app import create_app
from review.policy import PolicyNotFound, load_policy, recent_feedback
from rfa_common.models import Channel

REPO_DATA = Path(__file__).resolve().parents[2] / "data"


def write_feedback(path: Path, rows: list[dict | str]) -> None:
    lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fb(review_id: int, decision: str, at: str, scope: str = "public") -> dict:
    return {"at": at, "scope": scope, "review_id": review_id, "decision": decision}


def test_repo_public_policy_has_rules_referenced_by_demo():
    policy = load_policy(REPO_DATA / "policy", "public")
    for rule in ["[release-date]", "[model-name]", "[internal-metric]", "[infra]"]:
        assert rule in policy.official
    assert "[gpu-pool]" in policy.personal


def test_feedback_filtered_by_scope_rejects_first_then_newest(tmp_path):
    path = tmp_path / "feedback.jsonl"
    write_feedback(
        path,
        [
            fb(1, "approve", "2026-09-26T10:00:00Z"),
            fb(2, "reject", "2026-09-26T09:00:00Z"),
            fb(3, "reject", "2026-09-26T11:00:00Z"),
            fb(4, "reject", "2026-09-26T12:00:00Z", scope="internal"),
            "not json",
            "",
            json.dumps({"scope": "public"}),
        ],
    )
    items = recent_feedback(path, Channel.PUBLIC)
    assert [i.review_id for i in items] == [3, 2, 1]


def test_feedback_limit(tmp_path):
    path = tmp_path / "feedback.jsonl"
    write_feedback(path, [fb(i, "reject", f"2026-09-26T10:{i:02d}:00Z") for i in range(15)])
    items = recent_feedback(path, Channel.PUBLIC, limit=10)
    assert [i.review_id for i in items] == list(range(14, 4, -1))


def test_missing_feedback_file_is_empty(tmp_path):
    assert recent_feedback(tmp_path / "none.jsonl", Channel.PUBLIC) == []


@pytest.mark.parametrize("scope", ["internal", "slack", "../public"])
def test_unknown_or_unwritten_scope_raises(scope):
    with pytest.raises(PolicyNotFound):
        load_policy(REPO_DATA / "policy", scope)


def test_personal_is_optional(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "official.md").write_text("# rules", encoding="utf-8")
    assert load_policy(tmp_path, "public").personal == ""


def test_policy_endpoint(tmp_path):
    policy_dir = tmp_path / "policy" / "public"
    policy_dir.mkdir(parents=True)
    (policy_dir / "official.md").write_text("- [release-date] x", encoding="utf-8")
    write_feedback(
        tmp_path / "policy" / "feedback.jsonl", [fb(7, "reject", "2026-09-26T10:00:00Z")]
    )
    client = TestClient(create_app(tmp_path))

    body = client.get("/policy/public").json()
    assert body["scope"] == "public"
    assert body["official"] == "- [release-date] x"
    assert body["personal"] == ""
    assert [f["review_id"] for f in body["feedback"]] == [7]

    missing = client.get("/policy/internal")
    assert missing.status_code == 404
    assert missing.json() == {"error": "not_found", "scope": "internal"}
    assert client.get("/policy/bogus").status_code == 404
