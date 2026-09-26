from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from rfa_workflow.clients import KnowledgeClient, ReviewClient
from rfa_workflow.llm import LLM, make_llm


@dataclass(frozen=True)
class Deps:
    review: ReviewClient
    knowledge: KnowledgeClient
    llm: LLM

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Deps:
        env = os.environ if env is None else env
        return cls(
            review=ReviewClient.from_url(env.get("REVIEW_URL", "http://127.0.0.1:8790")),
            knowledge=KnowledgeClient.from_url(env.get("KNOWLEDGE_URL", "http://127.0.0.1:8791")),
            llm=make_llm(env),
        )
