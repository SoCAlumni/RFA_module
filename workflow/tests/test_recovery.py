"""실패 원인별 복구 경로: 자동 복구 성공 / 한도 초과 → needs_human."""

import json

import pytest
from rfa_workflow.graph_public import run
from test_graph import LEAKY, PASS, PICK, REDACT
from wf_support import FakeLLM, FlakyHttp, mention


def happy_llm(**overrides) -> FakeLLM:
    script = {"pick_task": [PICK], "writer": [LEAKY], "editor": [PASS], "censor_public": [REDACT]}
    script.update(overrides)
    return FakeLLM(script)


def doc(env, rid):
    return env.review_http.get(f"/reviews/{rid}").json()


def whats(d) -> list[str]:
    return [e["what"] for e in d["events"]]


# ---------- 일시적 오류: backoff 재시도 ----------


def test_transient_error_is_retried_with_backoff(env):
    http = FlakyHttp(env.review_http, {("post", "/reviews/1/draft"): ["connect", 503]})
    result = run(mention(), env.make_deps(happy_llm(), review_http_override=http))

    assert result.outcome == "reviewed"
    assert result.recoveries == [
        "재시도 POST /reviews/1/draft: ConnectError",
        "재시도 POST /reviews/1/draft: HTTP 503",
    ]
    assert env.sleeps == [0.5, 1.0]


def test_transient_error_beyond_retry_limit_goes_to_human(env):
    http = FlakyHttp(env.knowledge_http, {("get", "/tasks"): [503, 503, 503, 503]})
    result = run(mention(), env.make_deps(happy_llm(), knowledge_http_override=http))

    assert result.outcome == "needs_human"
    assert "GET /tasks: HTTP 503 (재시도 3회 후)" in result.summary
    assert env.sleeps == [0.5, 1.0, 2.0]
    d = doc(env, result.review_id)
    assert d["status"] == "needs_human"
    assert "자동 복구 3회" in d["events"][-1]["detail"]  # 사람이 결재 웹에서 복구 내역을 본다


def test_retry_after_lost_response_does_not_submit_twice(env):
    """서버는 초안을 받았는데 응답이 끊김 → 재시도는 409 → 최신 문서에 내 초안이 있으니 계속."""
    http = FlakyHttp(env.review_http, {("post", "/reviews/1/draft"): ["timeout_after"]})
    result = run(mention(), env.make_deps(happy_llm(), review_http_override=http))

    assert result.outcome == "reviewed"
    assert result.recoveries == [
        "재시도 POST /reviews/1/draft: ReadTimeout",
        "409 POST /reviews/1/draft: 이미 반영됨",
    ]
    d = doc(env, result.review_id)
    assert whats(d).count("drafted") == 1 and whats(d).count("scanned") == 1


# ---------- 409 상태 충돌 ----------


def test_conflict_with_human_handled_doc_returns_existing_state(env):
    def human_steps_in(inner):
        inner.post("/reviews/1/needs-human", json={"reason": "사람이 직접 답하기로 함"})

    http = FlakyHttp(env.review_http, {("post", "/reviews/1/verdict"): [human_steps_in]})
    result = run(mention(), env.make_deps(happy_llm(), review_http_override=http))

    assert result.outcome == "already_handled"
    assert result.summary == "이미 처리된 문서 (상태: needs_human)"
    d = doc(env, result.review_id)
    assert d["verdict"] is None  # 사람이 잡은 문서에 판정을 덮어쓰지 않았다
    assert d["events"][-1]["detail"] == "사람이 직접 답하기로 함"


def test_unresolvable_conflict_goes_to_human(env):
    http = FlakyHttp(env.review_http, {("post", "/reviews/1/verdict"): [409]})
    result = run(mention(), env.make_deps(happy_llm(), review_http_override=http))

    assert result.outcome == "needs_human"
    assert "문서가 scanned 상태라 진행 불가" in result.summary
    assert doc(env, result.review_id)["status"] == "needs_human"


def test_rerun_on_already_progressed_doc_is_reported_not_redone(env):
    first = run(mention(), env.make_deps(happy_llm()))
    llm = FakeLLM({})
    again = run(mention(), env.make_deps(llm))

    assert (again.outcome, again.review_id) == ("already_handled", first.review_id)
    assert again.summary == "이미 처리된 문서 (상태: reviewed)"
    assert llm.calls == []


# ---------- 관련 업무·지식 없음 ----------


def test_empty_answer_reselects_another_task(env):
    llm = happy_llm(pick_task=[{"task_id": "quantization", "reason": "?"}, PICK])
    result = run(mention(), env.make_deps(llm))

    assert result.outcome == "reviewed"
    assert result.recoveries == ["task 재선택 (답 없던 task: quantization)"]
    second_pick = [u for n, u in llm.calls if n == "pick_task"][1]
    assert "[이미 물어봤지만 답이 없던 task]\nquantization" in second_pick
    assert "quantization" not in second_pick.split("[task 목록]")[1].split("[이미")[0]


def test_no_task_returns_to_supervisor_then_hint_resumes_same_doc(env):
    none = {"task_id": "none", "reason": "무슨 모델인지 모름"}
    first = run(
        mention("@zetwhite 그거 어떻게 돼가요?"), env.make_deps(FakeLLM({"pick_task": [none]}))
    )

    assert first.outcome == "returned"
    assert first.target == "zetwhite/RFA_test#1"  # supervisor 가 get_thread 로 읽을 대상
    assert "관련 task 없음: 무슨 모델인지 모름" in first.summary
    assert doc(env, first.review_id)["status"] == "opened"  # 사람에게 넘기지 않았다

    llm = happy_llm()
    hint = "ORBIT 벤치마크 진행 상황을 묻는 것"
    second = run(mention("@zetwhite 그거 어떻게 돼가요?"), env.make_deps(llm), hint=hint)

    assert (second.outcome, second.review_id) == ("reviewed", first.review_id)
    assert f"[supervisor 보완]\n{hint}" in llm.calls[0][1]


def test_still_no_knowledge_after_hint_goes_to_human(env):
    none = {"task_id": "none", "reason": "여전히 모름"}
    run(mention(), env.make_deps(FakeLLM({"pick_task": [none]})))
    result = run(mention(), env.make_deps(FakeLLM({"pick_task": [none]})), hint="더 모름")

    assert result.outcome == "needs_human"
    assert doc(env, result.review_id)["status"] == "needs_human"


def test_two_empty_tasks_return_to_supervisor(env):
    picks = [{"task_id": "quantization", "reason": "?"}, {"task_id": "prism", "reason": "?"}]
    result = run(mention("@zetwhite xyzzy"), env.make_deps(FakeLLM({"pick_task": picks})))
    assert result.outcome == "returned"
    assert "물어본 task: quantization, prism" in result.summary


# ---------- 전체 복구 상한 ----------


def test_total_recovery_budget_is_shared_across_causes(env):
    """각각은 재시도 한도 안이지만 합계가 3회를 넘으면 사람에게."""
    knowledge = FlakyHttp(env.knowledge_http, {("get", "/tasks"): [503, 503]})
    review = FlakyHttp(env.review_http, {("post", "/reviews/1/draft"): ["connect", "connect"]})
    deps = env.make_deps(
        happy_llm(), review_http_override=review, knowledge_http_override=knowledge
    )

    result = run(mention(), deps)

    assert result.outcome == "needs_human"
    assert "자동 복구 한도(3회) 초과" in result.summary
    assert len(result.recoveries) == 3


# ---------- 중단 후 재실행: 중간 상태에서 재개 ----------


class Killed(Exception):
    """프로세스가 죽은 것을 흉내 (예상된 실패가 아니라 그래프 밖으로 튄다)."""


def test_resume_after_crash_at_knowledge_ready(env):
    with pytest.raises(Killed):
        run(mention(), env.make_deps(happy_llm(writer=[Killed()])))
    assert doc(env, 1)["status"] == "knowledge_ready"

    llm = FakeLLM({"writer": [LEAKY], "editor": [PASS], "censor_public": [REDACT]})
    result = run(mention(), env.make_deps(llm))

    assert (result.outcome, result.review_id) == ("reviewed", 1)
    assert result.resumed_from == "knowledge_ready"
    assert llm.count("pick_task") == 0  # 저장된 지식을 그대로 썼다
    assert "Nimbus2" in llm.calls[0][1]  # writer 입력에 저장된 지식
    assert whats(doc(env, 1)).count("knowledge_ready") == 1


def test_resume_after_crash_at_scanned(env):
    revise = {"verdict": "revise", "notes": "짧게", "issues": []}
    first = happy_llm(writer=["긴 초안", LEAKY], editor=[revise, PASS], censor_public=[Killed()])
    with pytest.raises(Killed):
        run(mention(), env.make_deps(first))
    assert doc(env, 1)["status"] == "scanned"

    llm = FakeLLM({"censor_public": [REDACT]})
    result = run(mention(), env.make_deps(llm))

    assert (result.outcome, result.resumed_from) == ("reviewed", "scanned")
    assert [n for n, _ in llm.calls] == ["censor_public"]  # 초안·첨삭을 다시 하지 않았다
    censor_user = llm.calls[0][1]
    assert LEAKY in censor_user and "private_ip: 10.12.3.4" in censor_user
    d = doc(env, 1)
    assert [e["verdict"] for e in d["edit_log"]] == ["revise", "pass"]  # 첨삭 이력 보존
    assert whats(d).count("drafted") == 1


def test_unresumable_state_goes_to_human_and_marks_the_doc(env, tmp_path):
    """drafted 는 정상 저장 경로에선 남지 않는다 → 상태 파일을 직접 바꿔 재현."""
    with pytest.raises(Killed):
        run(mention(), env.make_deps(happy_llm(writer=[Killed()])))
    path = tmp_path / "state" / "review-1.json"
    stuck = json.loads(path.read_text(encoding="utf-8"))
    stuck["status"] = "drafted"
    path.write_text(json.dumps(stuck), encoding="utf-8")

    llm = FakeLLM({})
    result = run(mention(), env.make_deps(llm))

    assert (result.outcome, result.review_id) == ("needs_human", 1)
    assert llm.calls == []
    d = doc(env, 1)
    assert d["status"] == "needs_human"  # 결과뿐 아니라 문서도 사람에게 넘어갔다
    assert "drafted 에서는 재개할 수 없음" in d["events"][-1]["detail"]


# ---------- 이미 반영된 요청은 예산과 무관하게 성공 ----------


def test_already_applied_request_succeeds_even_when_budget_is_spent(env):
    """재시도 2회 + verdict 응답 유실 재시도 1회 = 예산 3회 소진.

    이어진 409 조회로 저장이 확인되면 한도와 무관하게 성공 → reviewed 유지.
    """
    knowledge = FlakyHttp(env.knowledge_http, {("get", "/tasks"): [503, 503]})
    review = FlakyHttp(env.review_http, {("post", "/reviews/1/verdict"): ["timeout_after"]})
    deps = env.make_deps(
        happy_llm(), review_http_override=review, knowledge_http_override=knowledge
    )

    result = run(mention(), deps)

    assert result.outcome == "reviewed"
    assert result.recoveries[-1] == "409 POST /reviews/1/verdict: 이미 반영됨"
    assert len(result.recoveries) == 4
    assert doc(env, 1)["status"] == "reviewed"
