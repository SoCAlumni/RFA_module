"""게시자. 사람이 승인한 초안을 그 안건이 들어온 채널에 올린다.

결재 서버만 게시자를 부른다 (desk 에는 게시 경로가 없다).
env RFA_PUBLISHER 로 고른다: mock (기록만). 실제 채널 게시(live)는 Step 4 에서 추가.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from rfa_common.contracts import ChannelKind


class PublishError(Exception):
    """게시 실패 (채널 오류 등). 안건은 approved 에 머물고 다시 approve 하면 재시도한다."""


class Publisher(Protocol):
    def publish(self, channel: ChannelKind, target: str, body: str) -> str:
        """게시하고 게시물 URL 을 돌려준다. 실패는 PublishError."""
        ...


@dataclass
class MockPublisher:
    """채널을 부르지 않고 기록만 한다. fail 을 켜면 게시 실패를 흉내낸다."""

    fail: bool = False
    published: list[tuple[ChannelKind, str, str]] = field(default_factory=list)

    def publish(self, channel: ChannelKind, target: str, body: str) -> str:
        if self.fail:
            raise PublishError("mock channel down")
        self.published.append((channel, target, body))
        return f"mock://{channel}/{target}/{len(self.published)}"


def make_publisher(env: Mapping[str, str]) -> Publisher:
    kind = env.get("RFA_PUBLISHER", "mock")
    if kind == "mock":
        return MockPublisher()
    raise RuntimeError(f"unknown RFA_PUBLISHER: {kind!r} (mock)")
