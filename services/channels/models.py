from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ThreadComment(BaseModel):
    author: str
    body: str
    created_at: datetime
    url: str


class Thread(BaseModel):
    target: str = Field(description="owner/repo#N")
    title: str
    body: str
    state: str
    author: str
    url: str
    comments: list[ThreadComment] = Field(default_factory=list, description="최근 댓글, 오래된 순")
