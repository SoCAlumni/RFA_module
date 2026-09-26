"""Public 대응 그래프.

intake → ask_knowledge → write → edit ─(revise, rounds<2)→ write
                                     └(pass 또는 2회)→ submit → censor_public → END
어느 노드든 예상된 실패(ReviewConflict, ServiceError, LLMError, NoKnowledge) → needs_human → END
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from rfa_common.models import Mention

from rfa_workflow.clients import ReviewConflict, ServiceError
from rfa_workflow.deps import Deps
from rfa_workflow.llm import LLMError
from rfa_workflow.nodes.censor import censor_public
from rfa_workflow.nodes.intake import intake, needs_human
from rfa_workflow.nodes.knowledge import NoKnowledge, ask_knowledge
from rfa_workflow.nodes.press import edit, write
from rfa_workflow.nodes.submit import submit
from rfa_workflow.state import RunResult, State

MAX_EDIT_ROUNDS = 2
EXPECTED = (ReviewConflict, ServiceError, LLMError, NoKnowledge)

Node = Callable[[State, Deps], dict]


def _guarded(node: Node, deps: Deps) -> Callable[[State], dict]:
    def run(state: State) -> dict:
        try:
            return node(state, deps)
        except EXPECTED as exc:
            return {"failure": f"{node.__name__}: {exc}"}

    run.__name__ = node.__name__
    return run


def _next(target: str) -> Callable[[State], str]:
    return lambda state: "needs_human" if state.get("failure") else target


def _after_edit(state: State) -> str:
    if state.get("failure"):
        return "needs_human"
    if state.get("edit_notes") and state["rounds"] < MAX_EDIT_ROUNDS:
        return "write"
    return "submit"


def build_graph(deps: Deps):
    g = StateGraph(State)
    for node in (intake, ask_knowledge, write, edit, submit, censor_public):
        g.add_node(node.__name__, _guarded(node, deps))
    g.add_node("needs_human", lambda state: needs_human(state, deps))

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _next("ask_knowledge"), ["ask_knowledge", "needs_human"])
    g.add_conditional_edges("ask_knowledge", _next("write"), ["write", "needs_human"])
    g.add_conditional_edges("write", _next("edit"), ["edit", "needs_human"])
    g.add_conditional_edges("edit", _after_edit, ["write", "submit", "needs_human"])
    g.add_conditional_edges("submit", _next("censor_public"), ["censor_public", "needs_human"])
    g.add_conditional_edges("censor_public", _next(END), [END, "needs_human"])
    g.add_edge("needs_human", END)
    return g.compile()


def run(mention: Mention, deps: Deps) -> RunResult:
    final: State = build_graph(deps).invoke({"mention": mention})
    if final["outcome"] == "reviewed":
        summary = f"결재 대기: {final['verdict'].summary}"
    else:
        summary = final.get("failure", "")
    return RunResult(review_id=final.get("review_id"), outcome=final["outcome"], summary=summary)
