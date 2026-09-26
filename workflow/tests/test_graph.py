import pytest
from rfa_workflow.clients import ReviewConflict
from rfa_workflow.graph_public import run
from rfa_workflow.llm import LLMError, RuleLLM
from wf_support import FakeLLM, mention

LEAKY = (
    "Nimbus2 INT4 적용 후 EM이 0.5%p 하락했고 11/3 릴리즈 예정이에요. 평가는 10.12.3.4에서 돌려요."
)
PICK = {"task_id": "orbit", "reason": "ORBIT 질문"}
PASS = {"verdict": "pass", "notes": "", "issues": []}
REDACT = {
    "verdict": "redact",
    "redacted_body": "양자화 후 정확도가 소폭 하락해 보완 중이에요. 일정은 확정되면 공유드릴게요.",
    "reasons": [
        {"rule": "official:model-name", "span": "Nimbus2", "action": "blur"},
        {"rule": "scanner:private-ip", "span": "10.12.3.4", "action": "remove"},
    ],
    "summary": "모델명·IP 제거",
}


def doc(env, rid):
    return env.review_http.get(f"/reviews/{rid}").json()


def test_happy_path_reaches_reviewed(env):
    llm = FakeLLM(
        {"pick_task": [PICK], "writer": [LEAKY], "editor": [PASS], "censor_public": [REDACT]}
    )

    result = run(mention(), env.make_deps(llm))

    assert result.outcome == "reviewed"
    assert result.summary == "결재 대기: 모델명·IP 제거"
    d = doc(env, result.review_id)
    assert d["status"] == "reviewed"
    assert d["knowledge"]["task_id"] == "orbit"
    assert d["draft"] == LEAKY
    assert [h["match"] for h in d["scan"]] == ["10.12.3.4"]  # 호스트 스캐너가 실제로 돌았다
    assert d["final_body"] == REDACT["redacted_body"]
    assert [(e["who"], e["what"]) for e in d["events"]] == [
        ("intake", "opened"),
        ("knowledge", "knowledge_ready"),
        ("press", "drafted"),
        ("scanner", "scanned"),
        ("censor_public", "reviewed"),
    ]
    # censor 는 정책·스캔 결과·초안을 모두 받았다
    censor_user = next(u for n, u in llm.calls if n == "censor_public")
    assert "[release-date]" in censor_user and "private_ip: 10.12.3.4" in censor_user


def test_revise_then_pass_feeds_notes_back_to_writer(env):
    revise = {"verdict": "revise", "notes": "질문 범위만 두 문장으로", "issues": ["범위 초과"]}
    llm = FakeLLM(
        {
            "pick_task": [PICK],
            "writer": ["긴 초안", "짧은 초안"],
            "editor": [revise, PASS],
            "censor_public": [
                {"verdict": "allow", "redacted_body": "", "reasons": [], "summary": "ok"}
            ],
        }
    )

    result = run(mention(), env.make_deps(llm))

    assert result.outcome == "reviewed"
    second_writer = [u for n, u in llm.calls if n == "writer"][1]
    assert "[첨삭 의견]\n질문 범위만 두 문장으로" in second_writer
    assert "[이전 초안]\n긴 초안" in second_writer
    d = doc(env, result.review_id)
    assert d["draft"] == "짧은 초안"
    assert [(e["round"], e["verdict"]) for e in d["edit_log"]] == [(1, "revise"), (2, "pass")]


def test_edit_loop_stops_after_two_rounds(env):
    revise = {"verdict": "revise", "notes": "더 고쳐", "issues": []}
    llm = FakeLLM(
        {
            "pick_task": [PICK],
            "writer": ["v1", "v2", "v3"],
            "editor": [revise, revise, revise],
            "censor_public": [
                {"verdict": "allow", "redacted_body": "", "reasons": [], "summary": "ok"}
            ],
        }
    )

    result = run(mention(), env.make_deps(llm))

    assert result.outcome == "reviewed"
    assert (llm.count("writer"), llm.count("editor")) == (2, 2)
    assert doc(env, result.review_id)["draft"] == "v2"


def test_no_matching_task_goes_to_human(env):
    llm = FakeLLM({"pick_task": [{"task_id": "none", "reason": "점심 메뉴 질문"}]})
    result = run(mention("@zetwhite 점심 뭐 먹어요?"), env.make_deps(llm))
    assert result.outcome == "needs_human"
    assert "관련 task 없음" in result.summary
    d = doc(env, result.review_id)
    assert d["status"] == "needs_human"
    assert d["events"][-1]["who"] == "workflow"


def test_empty_knowledge_goes_to_human(env):
    llm = FakeLLM({"pick_task": [{"task_id": "quantization", "reason": "?"}]})
    result = run(mention("@zetwhite xyzzy"), env.make_deps(llm))
    assert result.outcome == "needs_human"
    assert "관련 지식 없음" in result.summary


def test_llm_refusal_goes_to_human(env):
    llm = FakeLLM({"pick_task": [PICK], "writer": [LLMError("writer: model refused")]})
    result = run(mention(), env.make_deps(llm))
    assert result.outcome == "needs_human"
    assert result.summary == "write: writer: model refused"
    assert doc(env, result.review_id)["status"] == "needs_human"


def test_censor_retries_once_on_bad_format(env):
    bad = {**REDACT, "reasons": [{"rule": "Official Release", "span": "x", "action": "remove"}]}
    llm = FakeLLM(
        {"pick_task": [PICK], "writer": [LEAKY], "editor": [PASS], "censor_public": [bad, REDACT]}
    )
    result = run(mention(), env.make_deps(llm))
    assert result.outcome == "reviewed"
    retry_user = [u for n, u in llm.calls if n == "censor_public"][1]
    assert "[이전 출력 오류" in retry_user


def test_censor_gives_up_after_two_bad_formats(env):
    bad = {**REDACT, "redacted_body": ""}  # redact 인데 수정본 없음
    llm = FakeLLM(
        {"pick_task": [PICK], "writer": [LEAKY], "editor": [PASS], "censor_public": [bad, bad]}
    )
    result = run(mention(), env.make_deps(llm))
    assert result.outcome == "needs_human"
    assert "invalid verdict" in result.summary
    assert doc(env, result.review_id)["status"] == "needs_human"


def test_review_conflict_goes_to_human(env):
    llm = FakeLLM(
        {"pick_task": [PICK], "writer": [LEAKY], "editor": [PASS], "censor_public": [REDACT]}
    )
    deps = env.make_deps(llm)

    def conflict(*_):
        raise ReviewConflict("POST verdict: already moved")

    deps.review.submit_verdict = conflict  # 누군가 먼저 문서를 옮긴 상황
    result = run(mention(), deps)
    assert result.outcome == "needs_human"
    assert "already moved" in result.summary


def test_rule_llm_runs_end_to_end_and_redacts_secrets(env):
    result = run(mention(), env.make_deps(RuleLLM()))

    assert result.outcome == "reviewed"
    d = doc(env, result.review_id)
    assert d["verdict"]["verdict"] == "redact"
    assert {h["type"] for h in d["scan"]} == {"token", "private_ip"}  # 초안에 둘 다 흘러갔다
    assert "10.12.3.4" not in d["final_body"]
    assert not any(h["type"] == "token" and h["match"] in d["final_body"] for h in d["scan"])


@pytest.mark.parametrize("text", ["@zetwhite ORBIT 진행?", "@zetwhite orbit 모델 벤치마크"])
def test_rule_llm_picks_task_case_insensitively(env, text):
    result = run(mention(text), env.make_deps(RuleLLM()))
    assert doc(env, result.review_id)["knowledge"]["task_id"] == "orbit"
