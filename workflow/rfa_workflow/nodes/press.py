"""언론사: writer 가 초안을 쓰고 editor 가 첨삭한다 (evaluator-optimizer)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from rfa_common.models import EditDecision, EditVerdict

from rfa_workflow.deps import Deps
from rfa_workflow.llm import prompt
from rfa_workflow.state import State


class EditOutput(BaseModel):
    verdict: Literal["pass", "revise"]
    notes: str
    issues: list[str]


def _knowledge_block(state: State) -> str:
    k = state["knowledge"]
    sources = "\n".join(f"- {s}" for s in k.sources) or "- (없음)"
    return f"[근거 지식]\n{k.answer}\n\n[출처]\n{sources}"


def write(state: State, deps: Deps) -> dict:
    user = f"[질문]\n{state['mention'].text}\n\n{_knowledge_block(state)}"
    if state.get("edit_notes"):
        user += f"\n\n[이전 초안]\n{state['draft']}\n\n[첨삭 의견]\n{state['edit_notes']}"
    draft = deps.llm.text(
        name="writer",
        system=prompt("writer") + "\n\n" + prompt("style_public"),
        user=user,
        context={"knowledge": state["knowledge"]},
    )
    return {"draft": draft}


def edit(state: State, deps: Deps) -> dict:
    out = deps.llm.structured(
        name="editor",
        system=prompt("editor") + "\n\n" + prompt("style_public"),
        user=f"[질문]\n{state['mention'].text}\n\n{_knowledge_block(state)}\n\n[초안]\n{state['draft']}",
        schema=EditOutput,
        context={"draft": state["draft"]},
    )
    round_no = state.get("rounds", 0) + 1
    verdict = EditVerdict(
        round=round_no,
        verdict=EditDecision(out.verdict),
        notes=out.notes or None,
        issues=out.issues,
    )
    return {
        "rounds": round_no,
        "edit_log": [*state.get("edit_log", []), verdict],
        "edit_notes": out.notes if out.verdict == "revise" else None,
    }
