"""게시자 인터페이스. 승인된 본문을 외부 채널에 올린다.

Step 5 에서는 MockPublisher 만 있다. Step 7 에서 GithubPublisher 가 추가되고,
어느 쪽이든 게시 전에 clearance 토큰을 검증해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from review.clearance import verify


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
        try:
            verify(self.clearance_key, token, target, body)
        except Exception as exc:
            raise PublishError(f"clearance rejected: {exc}") from exc
        if self.fail:
            raise PublishError("mock channel down")
        self.published.append((target, body, token))
        return f"mock://{target}/comment-{len(self.published)}"
