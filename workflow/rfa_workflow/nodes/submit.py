from __future__ import annotations

from rfa_workflow.deps import Deps
from rfa_workflow.state import State


def submit(state: State, deps: Deps) -> dict:
    """최종 초안 제출. 호스트가 비밀값 스캔을 자동으로 돌려 돌려준다 (drafted → scanned)."""
    review = deps.review.submit_draft(state["review_id"], state["draft"], state["edit_log"])
    return {"scan": review.scan}
