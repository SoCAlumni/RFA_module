from __future__ import annotations

from rfa_common.models import ReviewStatus

from rfa_workflow.clients import AlreadyHandled, ReviewConflict, ServiceError
from rfa_workflow.deps import Deps
from rfa_workflow.recovery import RecoveryExhausted
from rfa_workflow.state import State

S = ReviewStatus
# 워크플로 몫이 끝난 상태: 결재 대기(reviewed) 이후와 사람에게 넘어간 문서. 다시 하지 않는다.
DONE = frozenset({S.REVIEWED, S.APPROVED, S.POSTED, S.REJECTED, S.NEEDS_HUMAN})


def intake(state: State, deps: Deps) -> dict:
    """결재 문서를 연다 → 결재 웹에 알람이 뜬다.

    같은 멘션의 문서가 이미 있으면 서버가 그걸 돌려준다(멱등). 상태에 따라:
      opened           → 처음부터 (supervisor 재요청도 여기)
      knowledge_ready  → 저장된 지식으로 초안부터 재개
      scanned          → 저장된 초안·스캔 결과로 기밀검토부터 재개
      reviewed 이후    → 워크플로 몫이 끝남. 손대지 않고 그 상태를 보고 (already_handled)
    """
    review = deps.review.open(state["mention"])
    base = {"review_id": review.id, "rounds": 0, "edit_log": []}
    if review.status == S.OPENED:
        return {**base, "resume_at": "ask_knowledge"}
    if review.status in DONE:
        raise AlreadyHandled(review)
    if review.status == S.KNOWLEDGE_READY:
        return {
            **base,
            "knowledge": review.knowledge,
            "resume_at": "write",
            "resumed_from": review.status,
        }
    if review.status == S.SCANNED:
        return {
            "review_id": review.id,
            "knowledge": review.knowledge,
            "draft": review.draft,
            "edit_log": review.edit_log,
            "rounds": len(review.edit_log),
            "scan": review.scan,
            "resume_at": "censor_public",
            "resumed_from": review.status,
        }
    # drafted 는 scanned 와 한 번에 저장되므로 정상적으로는 남지 않는다.
    # 남아 있다면 사람이 봐야 한다.
    raise ReviewConflict(
        f"review {review.id}: {review.status} 에서는 재개할 수 없음", review_id=review.id
    )


def needs_human(state: State, deps: Deps) -> dict:
    """자동으로 끝낼 수 없음. 사유(와 복구 내역)를 결재 문서에 남기고 사람에게 넘긴다."""
    reason = state.get("failure") or "unknown"
    if deps.budget.log:
        reason += f" | 자동 복구 {deps.budget.used}회: {'; '.join(deps.budget.log)}"
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
