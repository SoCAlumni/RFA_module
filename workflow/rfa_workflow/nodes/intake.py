from __future__ import annotations

from rfa_common.models import ReviewStatus

from rfa_workflow.clients import AlreadyHandled, ReviewConflict, ServiceError
from rfa_workflow.deps import Deps
from rfa_workflow.recovery import RecoveryExhausted
from rfa_workflow.state import State


def intake(state: State, deps: Deps) -> dict:
    """결재 문서를 연다 → 결재 웹에 알람이 뜬다.

    같은 멘션의 문서가 이미 있으면 서버가 그걸 돌려준다. opened 면 이어서 진행하고
    (supervisor 재요청), 이미 진행됐거나 끝난 문서면 손대지 않고 그 상태를 보고한다.
    """
    review = deps.review.open(state["mention"])
    if review.status != ReviewStatus.OPENED:
        raise AlreadyHandled(review)
    return {"review_id": review.id, "rounds": 0, "edit_log": []}


def needs_human(state: State, deps: Deps) -> dict:
    """자동으로 끝낼 수 없음. 사유(와 복구 내역)를 결재 문서에 남기고 사람에게 넘긴다."""
    reason = state.get("failure") or "unknown"
    if deps.budget.log:
        reason += f" | 자동 복구 {len(deps.budget.log)}회: {'; '.join(deps.budget.log)}"
    if "review_id" in state:
        try:
            deps.review.needs_human(state["review_id"], reason)
        except (AlreadyHandled, ReviewConflict, ServiceError, RecoveryExhausted):
            pass  # 기록 실패해도 결과는 needs_human (문서는 사람이 결재 웹에서 본다)
    return {"outcome": "needs_human", "failure": reason}


def returned(state: State, deps: Deps) -> dict:
    """관련 업무·지식을 못 찾음. 문서는 opened 로 두고 supervisor 에게 사유를 돌려준다."""
    return {"outcome": "returned"}


def already_handled(state: State, deps: Deps) -> dict:
    return {"outcome": "already_handled"}
