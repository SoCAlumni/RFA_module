"""모든 채널이 지키는 약속. desk 는 poll 로 멘션을 받고, 결재 서버는 post 로 게시한다."""

from __future__ import annotations

from typing import Protocol

from rfa_common.contracts import ChannelKind, Mention

CONTEXT_LIMIT = 10  # 멘션에 붙이는 스레드 맥락, 최근 몇 개까지


class ChannelError(Exception):
    """채널 API 가 실패했다 (인증, 권한, 없는 자리, 네트워크 등)."""


class Channel(Protocol):
    kind: ChannelKind

    def poll(self) -> list[Mention]:
        """새 멘션. 한 번 돌려준 멘션은 다시 돌려주지 않는다. 각 멘션에 스레드 맥락을 붙인다."""
        ...

    def post(self, target: str, body: str) -> str:
        """target 자리에 답글을 달고 그 주소를 돌려준다. 사람이 승인한 본문만 넘길 것."""
        ...
