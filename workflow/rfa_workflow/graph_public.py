"""Public 대응 그래프.

    intake → ask_knowledge → write → edit ─(revise, rounds<2)→ write
      (중단된 문서: intake 가 knowledge_ready → write, scanned → censor_public 로 바로 보낸다)
                                         └(pass 또는 2회)→ submit → censor_public → END(reviewed)

실패는 종류별로 끝난다:
    NoKnowledge (hint 없을 때)      → returned        : supervisor 가 질문을 보완해 hint 와 재요청
    AlreadyHandled                  → already_handled : 문서가 이미 사람 손에 있음
    그 밖(한도 초과, 거절, 충돌 등) → needs_human
자동 복구(재시도, 409 해소, task 재선택)는 클라이언트/노드 안에서 일어나고
복구 예산(합계 3회)을 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from rfa_common.models import Mention

from rfa_workflow.clients import AlreadyHandled, ReviewConflict, ServiceError
from rfa_workflow.deps import Deps
from rfa_workflow.llm import LLMError
from rfa_workflow.nodes.censor import censor_public
from rfa_workflow.nodes.intake import already_handled, intake, needs_human, returned
from rfa_workflow.nodes.knowledge import NoKnowledge, ask_knowledge
from rfa_workflow.nodes.press import edit, write
from rfa_workflow.nodes.submit import submit
from rfa_workflow.recovery import RecoveryExhausted
from rfa_workflow.state import FailureKind, RunResult, State

MAX_EDIT_ROUNDS = 2
EXPECTED = (
    NoKnowledge,
    AlreadyHandled,
    ReviewConflict,
    ServiceError,
    LLMError,
    RecoveryExhausted,
)
TERMINAL = {"returned": "returned", "handled": "already_handled", "human": "needs_human"}

Node = Callable[[State, Deps], dict]


def _kind(exc: Exception, state: State) -> FailureKind:
    if isinstance(exc, NoKnowledge) and not state.get("hint"):
        return "returned"  # supervisor 재요청(hint)에서도 못 찾으면 사람에게
    if isinstance(exc, AlreadyHandled):
        return "handled"
    return "human"


def _guarded(node: Node, deps: Deps) -> Callable[[State], dict]:
    def run(state: State) -> dict:
        try:
            return node(state, deps)
        except EXPECTED as exc:
            update: dict = {"failure": f"{node.__name__}: {exc}", "failure_kind": _kind(exc, state)}
            if isinstance(exc, AlreadyHandled):
                update["review_id"] = exc.review.id
                update["handled_status"] = exc.review.status
            return update

    run.__name__ = node.__name__
    return run


def _next(target: str) -> Callable[[State], str]:
    def route(state: State) -> str:
        return TERMINAL[state["failure_kind"]] if state.get("failure") else target

    return route


def _after_intake(state: State) -> str:
    """처음이면 ask_knowledge, 중단된 문서면 저장된 데이터가 있는 다음 단계부터."""
    if state.get("failure"):
        return TERMINAL[state["failure_kind"]]
    return state["resume_at"]


def _after_edit(state: State) -> str:
    if state.get("failure"):
        return TERMINAL[state["failure_kind"]]
    if state.get("edit_notes") and state["rounds"] < MAX_EDIT_ROUNDS:
        return "write"
    return "submit"


def build_graph(deps: Deps):
    g = StateGraph(State)
    for node in (intake, ask_knowledge, write, edit, submit, censor_public):
        g.add_node(node.__name__, _guarded(node, deps))
    for node in (needs_human, returned, already_handled):
        g.add_node(node.__name__, lambda state, node=node: node(state, deps))

    ends = list(TERMINAL.values())
    g.add_edge(START, "intake")
    g.add_conditional_edges(
        "intake", _after_intake, ["ask_knowledge", "write", "censor_public", *ends]
    )
    g.add_conditional_edges("ask_knowledge", _next("write"), ["write", *ends])
    g.add_conditional_edges("write", _next("edit"), ["edit", *ends])
    g.add_conditional_edges("edit", _after_edit, ["write", "submit", *ends])
    g.add_conditional_edges("submit", _next("censor_public"), ["censor_public", *ends])
    g.add_conditional_edges("censor_public", _next(END), [END, *ends])
    for end in ends:
        g.add_edge(end, END)
    return g.compile()


def run(mention: Mention, deps: Deps, hint: str | None = None) -> RunResult:
    deps = deps.for_run()
    final: State = build_graph(deps).invoke({"mention": mention, "hint": hint})
    outcome = final["outcome"]
    if outcome == "reviewed":
        summary = f"결재 대기: {final['verdict'].summary}"
    elif outcome == "already_handled":
        summary = f"이미 처리된 문서 (상태: {final['handled_status']})"
    else:
        summary = final["failure"]
    return RunResult(
        review_id=final.get("review_id"),
        outcome=outcome,
        summary=summary,
        recoveries=list(deps.budget.log),
        resumed_from=final.get("resumed_from"),
    )
