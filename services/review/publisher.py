"""게시자 인터페이스. 승인된 본문을 외부 채널에 올린다.

어느 게시자든 게시 전에 clearance 토큰을 검증한다. 검증 실패면 채널을 호출하지 않는다.
review 앱은 env RFA_PUBLISHER=mock|github 로 고른다 (make_publisher).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from mcp_channels.github import GithubClient, GithubError

from review.clearance import ClearanceError, verify


class PublishError(Exception):
    """게시 실패 (채널 오류, 토큰 불량 등)."""


class Publisher(Protocol):
    def publish(self, target: str, body: str, token: str) -> str | None:
        """게시하고 게시물 URL(없으면 None)을 돌려준다. 실패는 PublishError."""
        ...


@dataclass
class MockPublisher:
    """기록만 하는 게시자. clearance 검증은 실제와 동일하게 수행한다."""

    clearance_key: str
    fail: bool = False
    published: list[tuple[str, str, str]] = field(default_factory=list)

    def publish(self, target: str, body: str, token: str) -> str | None:
        _check(self.clearance_key, token, target, body)
        if self.fail:
            raise PublishError("mock channel down")
        self.published.append((target, body, token))
        return f"mock://{target}/comment-{len(self.published)}"


@dataclass
class GithubPublisher:
    """승인된 본문을 GitHub 이슈/PR 댓글로 게시한다."""

    clearance_key: str
    client: GithubClient

    def publish(self, target: str, body: str, token: str) -> str | None:
        _check(self.clearance_key, token, target, body)
        try:
            return self.client.create_comment(target, body)
        except GithubError as exc:
            raise PublishError(str(exc)) from exc


def _check(key: str, token: str, target: str, body: str) -> None:
    try:
        verify(key, token, target, body)
    except ClearanceError as exc:
        raise PublishError(f"clearance rejected: {exc.reason}") from exc


def make_publisher(env: Mapping[str, str], clearance_key: str) -> Publisher:
    kind = env.get("RFA_PUBLISHER", "mock")
    if kind == "mock":
        return MockPublisher(clearance_key=clearance_key)
    if kind == "github":
        token = env.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("RFA_PUBLISHER=github requires GITHUB_TOKEN")
        return GithubPublisher(clearance_key=clearance_key, client=GithubClient(token))
    raise RuntimeError(f"unknown RFA_PUBLISHER: {kind!r} (mock | github)")
