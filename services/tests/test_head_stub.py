from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from head_stub.app import NO_TASK, NOTHING_LEFT, create_app, sentences
from head_stub.loader import Doc, load_docs, load_tasks
from head_stub.rank import score, tokenize, top_docs
from rfa_common.contracts import AskResponse

REPO_DATA = Path(__file__).resolve().parents[2] / "data"
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC).isoformat()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(REPO_DATA))


def ask(client: TestClient, question: str, reasons: list[str] = ()) -> AskResponse:
    body = {
        "question": question,
        "channel": "github",
        "audience": "public",
        "target": "team/rfa-test#34",
        "url": "https://github.com/team/rfa-test/issues/34",
        "requester": "outside-dev",
        "feedback": [{"draft": "이전 초안", "reason": r, "at": NOW} for r in reasons],
    }
    res = client.post("/ask", json=body)
    assert res.status_code == 200, res.text
    return AskResponse.model_validate(res.json())


# ---- POST /ask ---------------------------------------------------------------


def test_ask_picks_task_and_returns_its_sentences(client):
    res = ask(client, "@zetwhite ORBIT 벤치마크 진행 어때?")
    assert res.task is not None and res.task.id == "orbit"
    assert res.task.name == "ORBIT 모델 벤치마크"
    assert res.refusal is None
    assert "EM이 FP16 대비 0.5%p 하락" in res.knowledge
    # stub 은 검열하지 않는다: 데모 지식의 기밀이 그대로 흐른다
    assert "11/3" in res.knowledge


def test_ask_other_task(client):
    res = ask(client, "PRISM 설계 문서 어디서 볼 수 있어?")
    assert res.task is not None and res.task.id == "prism"
    assert "YAML" in res.knowledge


def test_ask_without_match_refuses(client):
    res = ask(client, "zzzz qqqq")
    assert res == AskResponse(knowledge="", task=None, refusal=NO_TASK)


def test_feedback_reason_removes_matching_sentences(client):
    first = ask(client, "ORBIT 벤치마크 진행 어때?")
    second = ask(client, "ORBIT 벤치마크 진행 어때?", ["릴리즈 날짜가 들어가 있음"])
    assert "릴리즈" in first.knowledge
    assert "릴리즈" not in second.knowledge
    assert "EM이 FP16 대비 0.5%p 하락" in second.knowledge
    assert second.task == first.task


def test_feedback_accumulates_across_rounds(client):
    res = ask(client, "ORBIT 벤치마크 진행 어때?", ["릴리즈 일정 빼줘", "GPU pool 주소 빼줘"])
    assert "릴리즈" not in res.knowledge
    assert "gpu" not in res.knowledge.lower()


def test_feedback_that_removes_everything_refuses_but_keeps_task(client):
    res = ask(client, "PRISM 설계", ["PRISM 러너 얘기는 빼줘"])  # 세 문장 모두에 걸림
    assert res.knowledge == "" and res.refusal == NOTHING_LEFT
    assert res.task is not None and res.task.id == "prism"


def test_ask_rejects_target_of_wrong_channel(client):
    body = {
        "question": "q",
        "channel": "slack",
        "audience": "company",
        "target": "team/rfa-test#34",
        "url": "https://slack.com/archives/C1/p1",
        "requester": "r",
    }
    assert client.post("/ask", json=body).status_code == 422


def test_sentences_skip_headings_and_keep_decimals():
    body = "# 제목\n\nNimbus2 0.6B를 돌렸다. 주소는 10.12.3.4:8000 이다.\n다음 줄!"
    assert sentences(body) == ["Nimbus2 0.6B를 돌렸다.", "주소는 10.12.3.4:8000 이다.", "다음 줄!"]


# ---- loader / rank -----------------------------------------------------------


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_loader_skips_docs_without_frontmatter_and_dirs_without_task_file(tmp_path):
    root = tmp_path / "knowledge"
    _write(root / "t1" / "_task.yaml", "name: T1\ndescription: d\nupdated_at: 2026-09-01\n")
    _write(root / "t1" / "ok.md", "---\ntitle: OK\nupdated_at: 2026-09-02\n---\n본문 첫 줄\n")
    _write(root / "t1" / "nofm.md", "그냥 본문\n")
    _write(root / "t1" / "badfm.md", "---\ntitle: X\n---\n날짜 없음\n")
    _write(root / "t1" / "open.md", "---\ntitle: Y\nupdated_at: 2026-09-02\n본문만 있음\n")
    _write(root / "notask" / "a.md", "---\ntitle: A\nupdated_at: 2026-09-02\n---\nx\n")
    _write(root / "badmeta" / "_task.yaml", "name: only\n")

    assert [t.id for t in load_tasks(root)] == ["t1"]
    docs = load_docs(root, "t1")
    assert [(d.task_id, d.title) for d in docs] == [("t1", "OK")]
    assert docs[0].body == "본문 첫 줄"
    assert load_docs(root, "notask") == []


def test_demo_data_has_three_tasks():
    assert [t.id for t in load_tasks(REPO_DATA / "knowledge")] == ["orbit", "prism", "quantization"]


def _doc(title: str, body: str, day: int = 1) -> Doc:
    return Doc(task_id="t", title=title, updated_at=date(2026, 9, day), body=body)


def test_tokenize_and_score_use_substring_match_for_korean_particles():
    doc = _doc("벤치마크는 진행 중", "ORBIT 결과")
    tokens = tokenize("ORBIT 벤치마크 진행 어때?")
    assert tokens == ["orbit", "벤치마크", "어때", "진행"]
    assert score(tokens, doc) == pytest.approx(3 / 4)
    assert score([], doc) == 0.0


def test_top_docs_orders_by_score_then_filters_zero():
    a = _doc("a", "양자화 결과", 1)
    b = _doc("b", "양자화 결과 EM", 2)
    c = _doc("c", "무관", 3)
    picked = top_docs("양자화 EM 결과", [a, b, c], k=3)
    assert [d.title for d, _ in picked] == ["b", "a"]
    assert picked[0][1] == 1.0
    # 동점이면 최신 문서 먼저, k 로 잘림
    tie = top_docs("양자화", [a, b, c], k=1)
    assert [d.title for d, _ in tie] == ["b"]
