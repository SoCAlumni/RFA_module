"""Slack 채널 — 봇이 아니라 "나"로서 동작한다 (User Token, xoxp-).

GitHub 채널(내 PAT, @내 아이디)과 같은 구조다.
- 받기: Socket Mode(App Token, xapp-)로 user 이벤트를 받는다. 공개 URL 이 필요 없다.
    · 나에게 온 DM(channel_type=im) 은 전부
    · 내가 속한 채널의 메시지는 본문에 <@내ID> 가 있는 것만
    · 내가 보낸 메시지는 무시한다. 비서 답글도 내 이름으로 달리므로
      이 규칙이 자기 답글 루프를 막는다.
      (그래서 Slack 에서는 내가 나를 멘션해도 받지 않는다 — 다른 계정이 보내야 한다)
- 게시: chat.postMessage 로 그 스레드에 내 이름으로 답한다.

start()(Socket 연결)는 desk 만 부른다. 결재 서버는 post() 만 쓴다 — 연결이 둘이면 Slack 이
이벤트를 나눠 보내 desk 가 멘션을 놓친다.

target 형식: "<channel_id>/<thread_ts>" (예: C0123ABC/1727000000.000100)
"""

from __future__ import annotations

import logging
import queue
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from rfa_common.contracts import ChannelKind, Mention, ThreadMessage
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from channels.base import CONTEXT_LIMIT, ChannelError

log = logging.getLogger("rfa.slack")

SEEN_EVENTS = 500  # 중복 이벤트(Slack 재전송)를 거르려고 기억하는 event_id 수


class SlackError(ChannelError):
    """Slack API 가 실패했다."""


def message_url(channel: str, ts: str) -> str:
    return f"https://slack.com/archives/{channel}/p{ts.replace('.', '')}"


class SlackChannel:
    kind = ChannelKind.SLACK

    def __init__(
        self,
        web: WebClient,
        app_token: str,
        socket_factory: Callable[..., Any] = SocketModeClient,
    ) -> None:
        self._web = web
        self._app_token = app_token
        self._socket_factory = socket_factory
        self._socket: Any = None
        self._my_id: str | None = None
        self._events: queue.Queue[dict] = queue.Queue()  # 소켓 스레드 → poll
        self._seen: deque[str] = deque(maxlen=SEEN_EVENTS)
        self._names: dict[str, str] = {}

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> SlackChannel:
        missing = [k for k in ("SLACK_USER_TOKEN", "SLACK_APP_TOKEN") if not env.get(k)]
        if missing:
            raise RuntimeError(f"missing env: {', '.join(missing)} (see .env.example)")
        return cls(WebClient(token=env["SLACK_USER_TOKEN"]), env["SLACK_APP_TOKEN"])

    # ---- 받기 (desk) ----------------------------------------------------------------

    def start(self) -> None:
        """내 user ID 를 알아내고 Socket Mode 로 연결한다. desk 가 한 번 부른다."""
        try:
            self._my_id = self._web.auth_test()["user_id"]
        except (SlackApiError, OSError) as exc:
            raise SlackError(f"auth.test: {_why(exc)}") from exc
        self._socket = self._socket_factory(app_token=self._app_token, web_client=self._web)
        self._socket.socket_mode_request_listeners.append(self._on_request)
        self._socket.connect()
        log.info("slack: %s 로 이벤트 받는 중", self._my_id)

    def _on_request(self, client: Any, req: SocketModeRequest) -> None:
        """소켓 스레드에서 불린다. 바로 ack 하고, 나에게 온 메시지만 큐에 넣는다."""
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type != "events_api":
            return
        event_id = req.payload.get("event_id")
        if event_id in self._seen:
            return
        self._seen.append(event_id)
        event = req.payload.get("event", {})
        if self._is_for_me(event):
            self._events.put(event)

    def _is_for_me(self, event: dict) -> bool:
        if event.get("type") != "message" or event.get("subtype") or event.get("bot_id"):
            return False  # 수정·삭제·입장 알림, 봇 메시지
        if event.get("user") == self._my_id:
            return False  # 내가 보낸 메시지 (비서 답글 포함)
        if event.get("channel_type") == "im":
            return True
        return f"<@{self._my_id}>" in (event.get("text") or "")

    def poll(self) -> list[Mention]:
        """start() 이후 들어온 나에게 온 메시지를 Mention 으로. 스레드 맥락을 붙인다."""
        mentions = []
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return mentions
            mentions.append(self._mention(event))

    def _mention(self, event: dict) -> Mention:
        channel, ts = event["channel"], event["ts"]
        thread_ts = event.get("thread_ts") or ts
        text = (event.get("text") or "").replace(f"<@{self._my_id}>", "").strip()
        return Mention(
            channel=ChannelKind.SLACK,
            target=f"{channel}/{thread_ts}",
            author=self._name(event.get("user", "")),
            text=text,
            url=message_url(channel, ts),
            created_at=datetime.fromtimestamp(float(ts), UTC),
            context=self._context(channel, thread_ts, exclude_ts=ts),
        )

    def _context(self, channel: str, thread_ts: str, exclude_ts: str) -> list[ThreadMessage]:
        """같은 스레드의 최근 메시지, 오래된 순. 조회가 실패하면 맥락 없이 (멘션은 잃지 않는다)."""
        try:
            replies = self._web.conversations_replies(channel=channel, ts=thread_ts)["messages"]
        except (SlackApiError, OSError) as exc:
            log.warning("slack: 스레드 맥락을 못 읽음 (%s)", _why(exc))
            return []
        return [
            ThreadMessage(
                author=self._name(m.get("user", "")),
                text=m.get("text") or "",
                at=datetime.fromtimestamp(float(m["ts"]), UTC),
            )
            for m in replies
            if m["ts"] != exclude_ts
        ][-CONTEXT_LIMIT:]

    def _name(self, user_id: str) -> str:
        """user ID → 표시 이름. 못 읽으면 ID 그대로."""
        if user_id not in self._names:
            try:
                profile = self._web.users_info(user=user_id)["user"]
                self._names[user_id] = profile.get("real_name") or profile["name"]
            except (SlackApiError, OSError, KeyError):
                return user_id
        return self._names[user_id]

    # ---- 게시 (결재 서버) ------------------------------------------------------------

    def post(self, target: str, body: str) -> str:
        """그 스레드에 내 이름으로 답한다."""
        channel, _, thread_ts = target.partition("/")
        try:
            res = self._web.chat_postMessage(channel=channel, thread_ts=thread_ts, text=body)
        except (SlackApiError, OSError) as exc:
            raise SlackError(f"chat.postMessage {target}: {_why(exc)}") from exc
        return message_url(channel, res["ts"])


def _why(exc: Exception) -> str:
    """Slack 오류 코드(예: channel_not_found), 네트워크 오류면 그 종류."""
    return exc.response["error"] if isinstance(exc, SlackApiError) else type(exc).__name__
