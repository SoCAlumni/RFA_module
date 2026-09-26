"""모듈 간 계약의 데이터 모델 (contracts/*.openapi.yaml 과 1:1).

pydantic v2.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, model_validator

TARGET_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*$"
RULE_PATTERN = r"^(official|personal|scanner|editor):[a-z0-9-]+$"

Target = Annotated[str, Field(pattern=TARGET_PATTERN, description="owner/repo#number")]
Rule = Annotated[str, Field(pattern=RULE_PATTERN, description="scope:rule-id")]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Channel(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"


class KnowledgeResult(BaseModel):
    task_id: str
    answer: str
    confidence: Confidence
    sources: list[str] = Field(default_factory=list, description="근거 한 줄씩")


class TaskInfo(BaseModel):
    id: str
    name: str
    description: str
    updated_at: date


class Mention(BaseModel):
    channel: Channel
    target: Target
    author: str
    text: str
    url: HttpUrl
    created_at: datetime


class ReviewStatus(StrEnum):
    OPENED = "opened"
    KNOWLEDGE_READY = "knowledge_ready"
    DRAFTED = "drafted"
    SCANNED = "scanned"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    POSTED = "posted"
    NEEDS_HUMAN = "needs_human"


class EditDecision(StrEnum):
    PASS = "pass"
    REVISE = "revise"


class EditVerdict(BaseModel):
    round: Annotated[int, Field(ge=1)]
    verdict: EditDecision
    notes: str | None = None
    issues: list[str] = Field(default_factory=list)


class ScanType(StrEnum):
    TOKEN = "token"
    PRIVATE_IP = "private_ip"
    INTERNAL_HOST = "internal_host"
    INTERNAL_PATH = "internal_path"


class ScanHit(BaseModel):
    type: ScanType
    match: str
    span: tuple[int, int]


class ReasonAction(StrEnum):
    REMOVE = "remove"
    BLUR = "blur"
    KEEP = "keep"


class VerdictReason(BaseModel):
    rule: Rule
    span: str
    action: ReasonAction


class VerdictDecision(StrEnum):
    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"


class Verdict(BaseModel):
    verdict: VerdictDecision
    redacted_body: str | None = None
    reasons: list[VerdictReason] = Field(default_factory=list)
    summary: str

    @model_validator(mode="after")
    def _redact_requires_body(self) -> Verdict:
        if self.verdict == VerdictDecision.REDACT and not self.redacted_body:
            raise ValueError("redact verdict requires redacted_body")
        return self


class Event(BaseModel):
    at: datetime
    who: str
    what: str
    detail: str | None = None


class Decision(BaseModel):
    by: str
    at: datetime
    reason: str | None = None


class Review(BaseModel):
    id: int
    status: ReviewStatus
    channel: Channel
    target: Target
    source_url: HttpUrl
    requester: str
    question: str
    knowledge: KnowledgeResult | None = None
    draft: str | None = None
    edit_log: list[EditVerdict] = Field(default_factory=list)
    scan: list[ScanHit] = Field(default_factory=list)
    verdict: Verdict | None = None
    final_body: str | None = None
    decision: Decision | None = None
    events: list[Event] = Field(default_factory=list)
