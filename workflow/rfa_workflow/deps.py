from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from rfa_workflow.clients import ApprovalsClient, HeadClient
from rfa_workflow.llm import LLM, make_llm

DEFAULT_HEAD_URL = "http://127.0.0.1:8791"
DEFAULT_APPROVALS_URL = "http://127.0.0.1:8790"


@dataclass(frozen=True)
class Deps:
    """그래프 노드가 쓰는 바깥 세계. 테스트에서는 가짜로 바꿔 끼운다."""

    head: HeadClient
    approvals: ApprovalsClient
    llm: LLM

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Deps:
        env = os.environ if env is None else env
        return cls(
            head=HeadClient.from_url(env.get("HEAD_URL") or DEFAULT_HEAD_URL),
            approvals=ApprovalsClient.from_url(env.get("APPROVALS_URL") or DEFAULT_APPROVALS_URL),
            llm=make_llm(env),
        )
