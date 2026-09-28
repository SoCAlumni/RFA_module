"""팀 경계 두 곳의 데이터 모델 (contracts/*.openapi.yaml 과 1:1).

- contracts/head.openapi.yaml      : desk → head agent (민섭님).   AskRequest / AskResponse
- contracts/approvals.openapi.yaml : desk·프런트 → approvals (우리). Approval 과 요청 본문들

pydantic v2. 필드 이름은 yaml 의 properties 와 같아야 한다 (test_contracts.py 가 대조).
채널 어댑터가 만드는 Mention 은 yaml 에 없지만 같은 target 검증을 쓰므로 여기 둔다.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, model_validator


class ChannelKind(StrEnum):
    GITHUB = "github"
    SLACK = "slack"


class Audience(StrEnum):
    """답이 나갈 독자. head agent 가 검열 기준을 고르는 데 쓴다."""

    PUBLIC = "public"
    COMPANY = "company"


# 채널이 독자를 정한다.
AUDIENCE_OF: dict[ChannelKind, Audience] = {
    ChannelKind.GITHUB: Audience.PUBLIC,
    ChannelKind.SLACK: Audience.COMPANY,
}

# target: 채널 안에서 답글이 달릴 자리.
#   github → owner/repo#N            (이슈/PR 번호)
#   slack  → C0123ABC/1727000000.000100   (채널 id / 스레드 ts)
TARGET_PATTERN: dict[ChannelKind, re.Pattern[str]] = {
    ChannelKind.GITHUB: re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*$"),
    ChannelKind.SLACK: re.compile(r"^[A-Z][A-Z0-9]+/[0-9]+\.[0-9]+$"),
}


def check_target(channel: ChannelKind, target: str) -> str:
    if not TARGET_PATTERN[channel].fullmatch(target):
        raise ValueError(f"target {target!r} does not match {channel} format")
    return target


class _Located(BaseModel):
    """channel 과 target 을 갖는 모델의 공통 검증."""

    channel: ChannelKind
    target: str

    @model_validator(mode="after")
    def _target_matches_channel(self):
        check_target(self.channel, self.target)
        return self


class ThreadMessage(BaseModel):
    author: str
    text: str
    at: datetime


class Mention(_Located):
    """채널에서 들어온 질문 하나. 채널 어댑터가 만들고 desk 가 그래프에 넘긴다."""

    author: str
    text: str
    url: HttpUrl
    created_at: datetime
    context: list[ThreadMessage] = Field(
        default_factory=list, description="같은 스레드의 최근 메시지, 오래된 순"
    )

    @property
    def audience(self) -> Audience:
        return AUDIENCE_OF[self.channel]


class Rejection(BaseModel):
    """사람이 거절한 초안과 그 이유. head agent 에 feedback 으로 돌아간다."""

    draft: str
    reason: str
    at: datetime


class TaskRef(BaseModel):
    """head agent 가 고른 업무. 프런트의 task 별 목록에 쓴다."""

    id: str
    name: str


# ---- head.openapi.yaml -------------------------------------------------------


class AskRequest(_Located):
    question: str
    audience: Audience
    url: HttpUrl
    requester: str
    context: list[ThreadMessage] = Field(default_factory=list)
    feedback: list[Rejection] = Field(
        default_factory=list, description="이 안건에서 거절된 초안들. 첫 요청은 빈 배열"
    )


class AskResponse(BaseModel):
    knowledge: str = Field(description="검열이 끝난 텍스트. 그대로 써도 되는 것만")
    task: TaskRef | None = None
    refusal: str | None = Field(
        None, description="답할 수 없을 때 사유. 이때 knowledge 는 빈 문자열"
    )

    @model_validator(mode="after")
    def _empty_needs_refusal(self):
        if not self.knowledge.strip() and self.refusal is None:
            raise ValueError("knowledge is empty but refusal is not given")
        return self


# ---- approvals.openapi.yaml --------------------------------------------------


class ApprovalStatus(StrEnum):
    PENDING = "pending"  # 사람 결재 대기
    APPROVED = "approved"  # 승인됨. 게시 중(게시 실패 시 여기 머묾)
    POSTED = "posted"  # 채널에 게시 완료
    REJECTED = "rejected"  # 거절됨. desk 가 다시 써서 revise 하면 pending 으로
    CLOSED = "closed"  # 3번 거절되어 닫힘


class Event(BaseModel):
    at: datetime
    who: str
    what: str
    detail: str | None = None


class _Draft(BaseModel):
    """desk 가 만들어 올리는 내용. create 와 revise 가 공유."""

    task: TaskRef | None = None
    knowledge: str
    refusal: str | None = None
    draft: str


class CreateApprovalRequest(_Located, _Draft):
    audience: Audience
    source_url: HttpUrl = Field(description="멘션의 주소. 같은 주소면 안건을 새로 만들지 않는다")
    requester: str
    question: str
    context: list[ThreadMessage] = Field(default_factory=list)


class ReviseApprovalRequest(_Draft):
    pass


class RejectRequest(BaseModel):
    reason: Annotated[str, Field(min_length=1)]


class ApproveRequest(BaseModel):
    """승인 본문(선택). 사람이 고친 최종 초안. 없거나 비면 저장된 초안을 게시한다."""

    draft: str | None = None


class Edit(BaseModel):
    """사람이 승인하며 고치기 전의 원래 초안."""

    draft: str
    at: datetime


class Approval(CreateApprovalRequest):
    id: int
    status: ApprovalStatus
    round: Annotated[int, Field(ge=1)] = 1
    rejections: list[Rejection] = Field(default_factory=list)
    edits: list[Edit] = Field(
        default_factory=list, description="승인 때 사람이 고친 경우 원래 초안(고친 본문은 draft)"
    )
    posted_url: str | None = None
    events: list[Event] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ApprovalSummary(BaseModel):
    """상태별 건수. 프런트 사이드바용. 예: by_channel["slack"]["pending"] == 2"""

    by_channel: dict[str, dict[str, int]]
    by_task: dict[str, dict[str, int]] = Field(description="task 가 없는 안건은 키 '(none)'")
