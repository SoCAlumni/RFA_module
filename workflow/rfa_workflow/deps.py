from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from rfa_workflow.clients import KnowledgeClient, ReviewClient
from rfa_workflow.llm import LLM, make_llm
from rfa_workflow.recovery import RecoveryBudget


@dataclass(frozen=True)
class Deps:
    review: ReviewClient
    knowledge: KnowledgeClient
    llm: LLM
    budget: RecoveryBudget

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Deps:
        env = os.environ if env is None else env
        return cls(
            review=ReviewClient.from_url(env.get("REVIEW_URL", "http://127.0.0.1:8790")),
            knowledge=KnowledgeClient.from_url(env.get("KNOWLEDGE_URL", "http://127.0.0.1:8791")),
            llm=make_llm(env),
            budget=RecoveryBudget(),
        )

    def for_run(self) -> Deps:
        """실행 한 번마다 새 복구 예산. 두 클라이언트가 같은 예산을 나눠 쓴다."""
        budget = RecoveryBudget(self.budget.limit)
        return Deps(
            review=self.review.with_budget(budget),
            knowledge=self.knowledge.with_budget(budget),
            llm=self.llm,
            budget=budget,
        )
