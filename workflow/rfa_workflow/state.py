from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel
from rfa_common.models import EditVerdict, KnowledgeResult, Mention, ScanHit, Verdict

Outcome = Literal["reviewed", "needs_human"]


class State(TypedDict, total=False):
    mention: Mention
    review_id: int
    knowledge: KnowledgeResult
    draft: str
    edit_notes: str | None
    edit_log: list[EditVerdict]
    rounds: int
    scan: list[ScanHit]
    verdict: Verdict
    failure: str  # 설정되면 다음 노드 대신 needs_human 으로 간다
    outcome: Outcome


class RunResult(BaseModel):
    review_id: int | None
    outcome: Outcome
    summary: str
