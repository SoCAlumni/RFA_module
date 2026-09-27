"""env 로 켤 채널을 고른다. desk(멘션 받기)와 결재 서버(게시)가 같은 함수를 쓴다.

RFA_CHANNELS=github,slack  쉼표로 구분. 비우면 채널 없음.
각 채널이 필요한 env 가 없으면 시작할 때 RuntimeError (조용히 빠지지 않게).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from rfa_common.contracts import ChannelKind

from channels.base import Channel
from channels.github import GithubChannel
from channels.slack import SlackChannel

DEFAULT_DATA_DIR = "./data"


def make_channels(env: Mapping[str, str]) -> dict[ChannelKind, Channel]:
    state_dir = Path(env.get("RFA_DATA_DIR") or DEFAULT_DATA_DIR) / "state"
    channels: dict[ChannelKind, Channel] = {}
    for name in (n.strip() for n in env.get("RFA_CHANNELS", "").split(",")):
        if not name:
            continue
        if name == ChannelKind.GITHUB:
            channels[ChannelKind.GITHUB] = GithubChannel.from_env(env, state_dir)
        elif name == ChannelKind.SLACK:
            channels[ChannelKind.SLACK] = SlackChannel.from_env(env)
        else:
            raise RuntimeError(f"unknown RFA_CHANNELS entry: {name!r} (github | slack)")
    return channels
