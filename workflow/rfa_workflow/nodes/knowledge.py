"""실무대장에게 질문한다. 어느 task 에 물을지는 LLM 이 고정된 목록(+ none) 중에서 고른다.

고른 task 가 빈 답을 주면 그 task 를 빼고 한 번 더 고른다(TASK_RESELECT, 복구 예산 사용).
그래도 없으면 NoKnowledge → supervisor 에게 돌려준다(returned).
supervisor 가 준 hint 는 질문에 붙인다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, create_model
from rfa_common.models import TaskInfo

from rfa_workflow.deps import Deps
from rfa_workflow.llm import prompt
from rfa_workflow.state import State

NO_TASK = "none"
TASK_RESELECT = 1


class NoKnowledge(Exception):
    """물어볼 task 가 없거나 실무대장이 아무 답도 못 줬다."""


def _pick_schema(ids: list[str]) -> type[BaseModel]:
    return create_model(
        "PickTask",
        task_id=(Literal[tuple([*ids, NO_TASK])], ...),
        reason=(str, ...),
    )


def question_of(state: State) -> str:
    question = state["mention"].text
    if state.get("hint"):
        question += f"\n\n[supervisor 보완]\n{state['hint']}"
    return question


def _pick(deps: Deps, question: str, tasks: list[TaskInfo], tried: list[str]) -> BaseModel:
    listing = "\n".join(f"- {t.id}: {t.name} — {t.description}" for t in tasks)
    user = f"[질문]\n{question}\n\n[task 목록]\n{listing}"
    if tried:
        user += f"\n\n[이미 물어봤지만 답이 없던 task]\n{', '.join(tried)}"
    return deps.llm.structured(
        name="pick_task",
        system=prompt("pick_task"),
        user=user,
        schema=_pick_schema([t.id for t in tasks]),
        context={"question": question, "tasks": tasks},
    )


def ask_knowledge(state: State, deps: Deps) -> dict:
    question = question_of(state)
    tasks = deps.knowledge.tasks()
    tried: list[str] = []
    for attempt in range(1 + TASK_RESELECT):
        candidates = [t for t in tasks if t.id not in tried]
        if not candidates:
            break
        if attempt:
            deps.budget.spend(f"task 재선택 (답 없던 task: {', '.join(tried)})")
        picked = _pick(deps, question, candidates, tried)
        if picked.task_id == NO_TASK:
            raise NoKnowledge(f"관련 task 없음: {picked.reason}")
        knowledge = deps.knowledge.ask(picked.task_id, question)
        if knowledge.answer.strip():
            deps.review.attach_knowledge(state["review_id"], knowledge)
            return {"knowledge": knowledge}
        tried.append(picked.task_id)
    raise NoKnowledge(f"관련 지식 없음 (물어본 task: {', '.join(tried)})")
