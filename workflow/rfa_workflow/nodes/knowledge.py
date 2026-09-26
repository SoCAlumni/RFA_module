"""실무대장에게 질문한다. 어느 task 에 물을지는 LLM 이 고정된 목록(+ none) 중에서 고른다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, create_model

from rfa_workflow.deps import Deps
from rfa_workflow.llm import prompt
from rfa_workflow.state import State

NO_TASK = "none"


class NoKnowledge(Exception):
    """물어볼 task 가 없거나 실무대장이 아무 답도 못 줬다."""


def _pick_schema(ids: list[str]) -> type[BaseModel]:
    return create_model(
        "PickTask",
        task_id=(Literal[tuple([*ids, NO_TASK])], ...),
        reason=(str, ...),
    )


def ask_knowledge(state: State, deps: Deps) -> dict:
    question = state["mention"].text
    tasks = deps.knowledge.tasks()
    listing = "\n".join(f"- {t.id}: {t.name} — {t.description}" for t in tasks)
    picked = deps.llm.structured(
        name="pick_task",
        system=prompt("pick_task"),
        user=f"[질문]\n{question}\n\n[task 목록]\n{listing}",
        schema=_pick_schema([t.id for t in tasks]),
        context={"question": question, "tasks": tasks},
    )
    if picked.task_id == NO_TASK:
        raise NoKnowledge(f"관련 task 없음: {picked.reason}")
    knowledge = deps.knowledge.ask(picked.task_id, question)
    if not knowledge.answer.strip():
        raise NoKnowledge(f"task {picked.task_id} 에 관련 지식 없음")
    deps.review.attach_knowledge(state["review_id"], knowledge)
    return {"knowledge": knowledge}
