from __future__ import annotations

from rfa_workflow.clients import ReviewConflict
from rfa_workflow.deps import Deps
from rfa_workflow.state import State


def intake(state: State, deps: Deps) -> dict:
    """결재 문서를 연다 → 결재 웹에 알람이 뜬다."""
    review = deps.review.open(state["mention"])
    return {"review_id": review.id, "rounds": 0, "edit_log": []}


def needs_human(state: State, deps: Deps) -> dict:
    """자동으로 끝낼 수 없음. 사유를 결재 문서에 남기고 사람에게 넘긴다."""
    reason = state.get("failure") or "unknown"
    if "review_id" in state:
        try:
            deps.review.needs_human(state["review_id"], reason)
        except ReviewConflict:
            pass  # 이미 끝난 문서면 그대로 둔다
    return {"outcome": "needs_human"}
