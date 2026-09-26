"""workflow 테스트 공용 도우미. (테스트는 conftest 대신 여기서 import 한다)"""

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from knowledge_stub.app import create_app as create_knowledge_app
from review.app import create_app as create_review_app
from rfa_common.models import Channel, Mention
from rfa_workflow.clients import KnowledgeClient, ReviewClient
from rfa_workflow.deps import Deps

REPO_DATA = Path(__file__).resolve().parents[2] / "data"
WF_CLEARANCE_KEY = "wf-test-key"


@dataclass
class FakeLLM:
    """호출 이름별로 미리 정한 답을 차례로 돌려준다. 값이 Exception 이면 던진다."""

    script: dict[str, list[Any]]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def _next(self, name: str, user: str) -> Any:
        self.calls.append((name, user))
        value = self.script[name].pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str:
        return self._next(name, user)

    def structured(self, *, name, system, user, schema, context):
        value = self._next(name, user)
        return schema.model_validate(value)

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)


@dataclass
class Env:
    review_http: TestClient
    make_deps: Callable[[Any], Deps]


def make_env(tmp_path: Path) -> Env:
    shutil.copytree(REPO_DATA / "policy", tmp_path / "policy")
    review_http = TestClient(create_review_app(tmp_path), client=("127.0.0.1", 1))
    knowledge_http = TestClient(create_knowledge_app(REPO_DATA))

    def make_deps(llm) -> Deps:
        return Deps(
            review=ReviewClient(review_http), knowledge=KnowledgeClient(knowledge_http), llm=llm
        )

    return Env(review_http=review_http, make_deps=make_deps)


def mention(text: str = "@zetwhite ORBIT 벤치마크 진행 어때?") -> Mention:
    return Mention(
        channel=Channel.PUBLIC,
        target="zetwhite/RFA_test#1",
        author="someone",
        text=text,
        url="https://github.com/zetwhite/RFA_test/issues/1#issuecomment-1",
        created_at=datetime(2026, 9, 27, 10, 0, tzinfo=UTC),
    )
