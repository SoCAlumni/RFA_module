from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field
from rfa_common.models import EditVerdict, KnowledgeResult, Mention, ScanHit, Verdict

# reviewed        : 결재 대기까지 도착 (정상)
# returned        : 관련 업무·지식을 못 찾음 → supervisor 가 질문을 보완해 hint 와 함께 다시 부른다
# already_handled : 문서가 이미 사람 손에 넘어갔거나 끝남 (그 상태를 그대로 보고)
# needs_human     : 복구 한도 초과, 또는 사람의 정보·판단이 필요
Outcome = Literal["reviewed", "returned", "already_handled", "needs_human"]
FailureKind = Literal["returned", "handled", "human"]


class State(TypedDict, total=False):
    mention: Mention
    hint: str | None  # supervisor 가 보완한 정보 (재요청일 때만)
    review_id: int
    knowledge: KnowledgeResult
    draft: str
    edit_notes: str | None
    edit_log: list[EditVerdict]
    rounds: int
    scan: list[ScanHit]
    verdict: Verdict
    failure: str  # 설정되면 failure_kind 에 따라 returned / already_handled / needs_human 으로
    failure_kind: FailureKind
    handled_status: str
    resume_at: str  # intake 가 정한 시작 노드 (중간 상태 재개)
    resumed_from: str  # 재개했다면 그때 문서 상태
    outcome: Outcome


class RunResult(BaseModel):
    review_id: int | None
    outcome: Outcome
    summary: str
    recoveries: list[str] = Field(
        default_factory=list, description="이번 실행에서 쓴 자동 복구 내역"
    )
    resumed_from: str | None = Field(None, description="중단된 문서를 이어서 처리했다면 그때 상태")
