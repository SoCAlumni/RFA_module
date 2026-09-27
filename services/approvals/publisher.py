"""게시자. 사람이 승인한 초안을 그 안건이 들어온 채널에 올린다.

결재 서버만 게시자를 부른다 (desk 에는 게시 경로가 없다).
env RFA_PUBLISHER 로 고른다: mock (기록만) | live (RFA_CHANNELS 로 켠 실제 채널에 게시).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from channels.base import Channel, ChannelError
from channels.registry import make_channels
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


@dataclass
class LivePublisher:
    """안건이 들어온 채널에 실제로 답글을 단다."""

    channels: Mapping[ChannelKind, Channel]

    def publish(self, channel: ChannelKind, target: str, body: str) -> str:
        found = self.channels.get(channel)
        if found is None:
            raise PublishError(f"{channel} 채널이 켜져 있지 않음 (RFA_CHANNELS)")
        try:
            return found.post(target, body)
        except ChannelError as exc:
            raise PublishError(str(exc)) from exc


def make_publisher(env: Mapping[str, str]) -> Publisher:
    kind = env.get("RFA_PUBLISHER", "mock")
    if kind == "mock":
        return MockPublisher()
    if kind == "live":
        return LivePublisher(make_channels(env))
    raise RuntimeError(f"unknown RFA_PUBLISHER: {kind!r} (mock | live)")
