"""workflow 테스트 공용 도우미. (테스트는 conftest 대신 여기서 import 한다)"""

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from knowledge_stub.app import create_app as create_knowledge_app
from review.app import create_app as create_review_app
from rfa_common.models import Channel, Mention
from rfa_workflow.clients import KnowledgeClient, ReviewClient
from rfa_workflow.deps import Deps
from rfa_workflow.recovery import RecoveryBudget

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
    knowledge_http: TestClient
    make_deps: Callable[..., Deps]
    sleeps: list[float]  # backoff 로 잔 시간 (실제로 자지 않는다)


def make_env(tmp_path: Path) -> Env:
    shutil.copytree(REPO_DATA / "policy", tmp_path / "policy")
    review_http = TestClient(create_review_app(tmp_path), client=("127.0.0.1", 1))
    knowledge_http = TestClient(create_knowledge_app(REPO_DATA))
    sleeps: list[float] = []

    def make_deps(llm, review_http_override=None, knowledge_http_override=None) -> Deps:
        return Deps(
            review=ReviewClient(review_http_override or review_http, sleep=sleeps.append),
            knowledge=KnowledgeClient(
                knowledge_http_override or knowledge_http, sleep=sleeps.append
            ),
            llm=llm,
            budget=RecoveryBudget(),
        )

    return Env(
        review_http=review_http, knowledge_http=knowledge_http, make_deps=make_deps, sleeps=sleeps
    )


def mention(text: str = "@zetwhite ORBIT 벤치마크 진행 어때?") -> Mention:
    return Mention(
        channel=Channel.PUBLIC,
        target="zetwhite/RFA_test#1",
        author="someone",
        text=text,
        url="https://github.com/zetwhite/RFA_test/issues/1#issuecomment-1",
        created_at=datetime(2026, 9, 27, 10, 0, tzinfo=UTC),
    )


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = f"injected {status_code}"

    def json(self) -> dict:
        return {"error": self.text}


@dataclass
class FlakyHttp:
    """진짜 TestClient 앞에 끼워 요청마다 장애를 주입한다.

    faults[(method, path)] 는 차례로 쓰이는 동작 목록:
      "connect"        → 연결 오류 (서버에 안 닿음)
      "timeout_after"  → 서버는 처리했는데 응답이 안 옴 (중복 제출 상황)
      int              → 그 상태코드의 가짜 응답 (서버에 안 닿음)
      callable         → callable(inner) 를 먼저 실행하고 요청은 그대로 전달
    목록이 비면 그대로 전달한다.
    """

    inner: Any
    faults: dict[tuple[str, str], list[Any]] = field(default_factory=dict)
    seen: list[tuple[str, str]] = field(default_factory=list)

    def _do(self, method: str, url: str, **kwargs: Any) -> Any:
        self.seen.append((method, url))
        queue = self.faults.get((method, url), [])
        action = queue.pop(0) if queue else None
        if action == "connect":
            raise httpx.ConnectError("injected")
        if isinstance(action, int):
            return FakeResponse(action)
        if callable(action):
            action(self.inner)
        res = getattr(self.inner, method)(url, **kwargs)
        if action == "timeout_after":
            raise httpx.ReadTimeout("injected")
        return res

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._do("get", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._do("post", url, **kwargs)
