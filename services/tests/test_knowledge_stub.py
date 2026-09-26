from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from knowledge_stub.app import create_app
from knowledge_stub.loader import Doc, load_docs, load_tasks
from knowledge_stub.rank import score, tokenize, top_docs

REPO_DATA = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(REPO_DATA))


def test_list_tasks_returns_three_demo_tasks(client):
    ids = [t["id"] for t in client.get("/tasks").json()]
    assert ids == ["orbit", "prism", "quantization"]


def test_ask_orbit_returns_progress_doc_as_source(client):
    res = client.post("/tasks/orbit/ask", json={"question": "ORBIT 벤치마크 진행 어때?"})
    assert res.status_code == 200
    body = res.json()
    assert body["task_id"] == "orbit"
    assert body["sources"][0].startswith("ORBIT 9월 진행 현황: ")
    assert "Nimbus2" in body["answer"]  # 기밀이 그대로 흘러야 censor 데모가 된다
    assert 0 < body["confidence"] <= 1


def test_ask_unknown_task_is_404(client):
    assert client.post("/tasks/nope/ask", json={"question": "?"}).status_code == 404


def test_ask_without_match_gives_empty_answer(client):
    body = client.post("/tasks/quantization/ask", json={"question": "zzzz"}).json()
    assert body == {"task_id": "quantization", "answer": "", "confidence": 0.0, "sources": []}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_loader_skips_docs_without_frontmatter_and_dirs_without_task_file(tmp_path):
    root = tmp_path / "knowledge"
    _write(root / "t1" / "_task.yaml", "name: T1\ndescription: d\nupdated_at: 2026-09-01\n")
    _write(root / "t1" / "ok.md", "---\ntitle: OK\nupdated_at: 2026-09-02\n---\n본문 첫 줄\n")
    _write(root / "t1" / "nofm.md", "그냥 본문\n")
    _write(root / "t1" / "badfm.md", "---\ntitle: X\n---\n날짜 없음\n")
    _write(root / "notask" / "a.md", "---\ntitle: A\nupdated_at: 2026-09-02\n---\nx\n")
    _write(root / "badmeta" / "_task.yaml", "name: only\n")

    assert [t.id for t in load_tasks(root)] == ["t1"]
    docs = load_docs(root, "t1")
    assert [d.title for d in docs] == ["OK"]
    assert docs[0].summary == "본문 첫 줄"  # summary 없으면 본문 첫 줄
    assert docs[0].source_line == "OK: 본문 첫 줄"
    assert load_docs(root, "notask") == []


def test_tokenize_and_score_use_substring_match_for_korean_particles():
    doc = Doc(
        title="벤치마크는 진행 중", summary="", updated_at=date(2026, 9, 1), body="ORBIT 결과"
    )
    tokens = tokenize("ORBIT 벤치마크 진행 어때?")
    assert tokens == ["orbit", "벤치마크", "어때", "진행"]
    assert score(tokens, doc) == pytest.approx(3 / 4)
    assert score([], doc) == 0.0


def test_top_docs_orders_by_score_then_filters_zero():
    a = Doc(title="a", summary="", updated_at=date(2026, 9, 1), body="양자화 결과")
    b = Doc(title="b", summary="", updated_at=date(2026, 9, 2), body="양자화 결과 EM")
    c = Doc(title="c", summary="", updated_at=date(2026, 9, 3), body="무관")
    picked = top_docs("양자화 EM 결과", [a, b, c], k=3)
    assert [d.title for d, _ in picked] == ["b", "a"]
    assert picked[0][1] == 1.0
    # 동점이면 최신 문서 먼저, k 로 잘림
    tie = top_docs("양자화", [a, b, c], k=1)
    assert [d.title for d, _ in tie] == ["b"]
