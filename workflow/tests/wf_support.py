"""workflow 테스트 공용 도우미. (테스트는 conftest 대신 여기서 import 한다)"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from approvals.app import create_app as create_approvals_app
from approvals.publisher import MockPublisher
from fastapi.testclient import TestClient
from head_stub.app import create_app as create_head_app
from rfa_common.contracts import ChannelKind, Mention, ThreadMessage
from rfa_workflow.clients import ApprovalsClient, HeadClient
from rfa_workflow.deps import Deps

REPO_DATA = Path(__file__).resolve().parents[2] / "data"
AT = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


@dataclass
class FakeLLM:
    """미리 정한 답을 차례로 돌려준다. 값이 Exception 이면 던진다. 받은 프롬프트를 기록한다."""

    replies: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def text(self, *, name: str, system: str, user: str, context: Mapping[str, Any]) -> str:
        self.calls.append({"name": name, "system": system, "user": user, "context": context})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@dataclass
class FlakyHttp:
    """진짜 TestClient 앞에 끼워 요청마다 장애를 주입한다.

    faults[(method, path)] 는 차례로 쓰이는 동작 목록:
      "connect" → 연결 오류 (서버에 안 닿음)
      int       → 그 상태코드의 가짜 응답 (서버에 안 닿음)
    목록이 비면 그대로 전달한다.
    """

    inner: Any
    faults: dict[tuple[str, str], list[Any]] = field(default_factory=dict)
    seen: list[tuple[str, str, Any]] = field(default_factory=list)

    def _do(self, method: str, url: str, **kwargs: Any) -> Any:
        self.seen.append((method, url, kwargs.get("json")))
        queue = self.faults.get((method, url), [])
        action = queue.pop(0) if queue else None
        if action == "connect":
            raise httpx.ConnectError("injected")
        if isinstance(action, int):
            return httpx.Response(action, text=f"injected {action}")
        return getattr(self.inner, method)(url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._do("get", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._do("post", url, **kwargs)


@dataclass
class Env:
    """head_stub 과 결재 서버를 메모리에서 띄우고, 그 사이에 장애를 끼울 수 있게 한 것."""

    head: FlakyHttp
    approvals: FlakyHttp
    sleeps: list[float]

    def deps(self, llm: Any) -> Deps:
        return Deps(
            head=HeadClient(self.head, sleep=self.sleeps.append),
            approvals=ApprovalsClient(self.approvals, sleep=self.sleeps.append),
            llm=llm,
        )

    def reject(self, approval_id: int, reason: str) -> None:
        res = self.approvals.inner.post(f"/approvals/{approval_id}/reject", json={"reason": reason})
        assert res.status_code == 200, res.text

    def approval(self, approval_id: int) -> dict:
        return self.approvals.inner.get(f"/approvals/{approval_id}").json()


def make_env(tmp_path: Path) -> Env:
    approvals = TestClient(create_approvals_app(tmp_path, publisher=MockPublisher(), env={}))
    return Env(
        head=FlakyHttp(TestClient(create_head_app(REPO_DATA))),
        approvals=FlakyHttp(approvals),
        sleeps=[],
    )


def github_mention(text: str = "@zetwhite ORBIT 벤치마크 진행 어때?") -> Mention:
    return Mention(
        channel=ChannelKind.GITHUB,
        target="zetwhite/rfa-test#1",
        author="outside-dev",
        text=text,
        url="https://github.com/zetwhite/rfa-test/issues/1#issuecomment-1",
        created_at=AT,
        context=[ThreadMessage(author="outside-dev", text="양자화 이후 정확도는요?", at=AT)],
    )


def slack_mention(text: str = "PRISM 설계 문서 어디서 볼 수 있어요?") -> Mention:
    return Mention(
        channel=ChannelKind.SLACK,
        target="C0123ABC/1727000000.000100",
        author="product-team",
        text=text,
        url="https://slack.com/archives/C0123ABC/p1727000000000100",
        created_at=AT,
    )
