"""SlackChannel — 가짜 WebClient 와 가짜 소켓 이벤트로. 실제 Slack 에는 연결하지 않는다."""

from datetime import UTC, datetime

import pytest
from channels.base import ChannelError
from channels.slack import SlackChannel, SlackError, message_url
from slack_sdk.errors import SlackApiError
from slack_sdk.socket_mode.request import SocketModeRequest

ME = "UME"
FRIEND = "UFRIEND"
CH = "C0TEST"
DM = "D0TEST"


class FakeWeb:
    """slack_sdk.WebClient 자리. 부른 메서드를 기록한다. errors[메서드] 가 있으면 던진다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.errors: dict[str, Exception] = {}
        self.replies: list[dict] = []

    def _call(self, name: str, **kwargs):
        self.calls.append((name, kwargs))
        if name in self.errors:
            raise self.errors[name]

    def auth_test(self):
        self._call("auth_test")
        return {"user_id": ME}

    def users_info(self, user):
        self._call("users_info", user=user)
        return {"user": {"name": user.lower(), "real_name": {FRIEND: "동료 김"}.get(user, "")}}

    def conversations_replies(self, channel, ts):
        self._call("conversations_replies", channel=channel, ts=ts)
        return {"messages": self.replies}

    def chat_postMessage(self, channel, thread_ts, text):
        self._call("chat_postMessage", channel=channel, thread_ts=thread_ts, text=text)
        return {"ts": "1790600000.000200"}


class FakeSocket:
    def __init__(self, app_token, web_client) -> None:
        self.app_token = app_token
        self.socket_mode_request_listeners: list = []
        self.connected = False
        self.acks: list[str] = []

    def connect(self) -> None:
        self.connected = True

    def send_socket_mode_response(self, response) -> None:
        self.acks.append(response.envelope_id)


def api_error(code: str) -> SlackApiError:
    return SlackApiError(code, {"ok": False, "error": code})


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def slack(web) -> SlackChannel:
    channel = SlackChannel(web, "xapp-test", socket_factory=FakeSocket)
    channel.start()
    return channel


def deliver(slack: SlackChannel, event: dict, event_id: str = "Ev1") -> FakeSocket:
    """소켓 스레드가 이벤트를 받은 것처럼 리스너를 부른다."""
    socket = slack._socket
    req = SocketModeRequest(
        type="events_api",
        envelope_id=f"env-{event_id}",
        payload={"event_id": event_id, "event": event},
    )
    for listener in socket.socket_mode_request_listeners:
        listener(socket, req)
    return socket


def message(text: str, user: str = FRIEND, channel_type: str = "channel", **extra) -> dict:
    channel = DM if channel_type == "im" else CH
    return {
        "type": "message",
        "user": user,
        "text": text,
        "channel": channel,
        "channel_type": channel_type,
        "ts": "1790515422.436419",
        **extra,
    }


# ---- 시작 ----------------------------------------------------------------------------


def test_start_learns_my_id_and_connects(slack, web):
    socket = slack._socket
    assert socket.connected and socket.app_token == "xapp-test"
    assert web.calls[0] == ("auth_test", {})


def test_start_with_bad_token_is_a_channel_error(web):
    web.errors["auth_test"] = api_error("invalid_auth")
    with pytest.raises(SlackError, match="auth.test: invalid_auth"):
        SlackChannel(web, "xapp", socket_factory=FakeSocket).start()


def test_from_env_requires_both_tokens():
    with pytest.raises(RuntimeError, match="SLACK_APP_TOKEN"):
        SlackChannel.from_env({"SLACK_USER_TOKEN": "xoxp-x"})
    assert isinstance(
        SlackChannel.from_env({"SLACK_USER_TOKEN": "a", "SLACK_APP_TOKEN": "b"}), SlackChannel
    )


# ---- 받기: 어떤 메시지를 멘션으로 보나 ------------------------------------------------------


def test_channel_mention_becomes_mention_without_my_tag(slack):
    socket = deliver(slack, message(f"<@{ME}> ORBIT 벤치마크 어때요?"))
    assert socket.acks == ["env-Ev1"]  # 바로 ack

    (m,) = slack.poll()
    assert (m.channel, m.target, m.author) == ("slack", f"{CH}/1790515422.436419", "동료 김")
    assert m.text == "ORBIT 벤치마크 어때요?"
    assert str(m.url) == f"https://slack.com/archives/{CH}/p1790515422436419"
    assert m.created_at == datetime.fromtimestamp(1790515422.436419, UTC)
    assert m.audience == "company"
    assert slack.poll() == []  # 큐를 비움


def test_every_dm_to_me_counts(slack):
    deliver(slack, message("멘션 없이 DM", channel_type="im"))
    (m,) = slack.poll()
    assert m.target.startswith(f"{DM}/") and m.text == "멘션 없이 DM"


@pytest.mark.parametrize(
    "event",
    [
        message("멘션 없는 채널 잡담"),
        message(f"<@{ME}> 내가 나를 멘션", user=ME),  # 비서 답글도 나로 오므로 전부 무시
        message(f"<@{ME}> 수정됨", subtype="message_changed"),
        message(f"<@{ME}> 봇", bot_id="B1"),
        {**message(f"<@{ME}> 반응"), "type": "reaction_added"},
    ],
    ids=["no-mention", "from-me", "subtype", "bot", "not-message"],
)
def test_ignored_events_still_acked(slack, event):
    socket = deliver(slack, event)
    assert socket.acks == ["env-Ev1"]
    assert slack.poll() == []


def test_duplicate_event_is_dropped(slack):
    deliver(slack, message(f"<@{ME}> 한 번만"), event_id="EvDup")
    deliver(slack, message(f"<@{ME}> 한 번만"), event_id="EvDup")  # Slack 재전송
    assert len(slack.poll()) == 1


def test_non_event_requests_are_acked_and_ignored(slack):
    socket = slack._socket
    req = SocketModeRequest(type="hello", envelope_id="env-h", payload={})
    socket.socket_mode_request_listeners[0](socket, req)
    assert socket.acks == ["env-h"] and slack.poll() == []


# ---- 스레드 맥락 -------------------------------------------------------------------------


def test_reply_in_thread_gets_thread_context(slack, web):
    web.replies = [
        {"user": FRIEND, "text": "스레드 시작 질문", "ts": "1790515000.000100"},
        {"user": ME, "text": "이전 답", "ts": "1790515100.000100"},
        {"user": FRIEND, "text": f"<@{ME}> 추가 질문", "ts": "1790515422.436419"},
    ]
    deliver(slack, message(f"<@{ME}> 추가 질문", thread_ts="1790515000.000100"))

    (m,) = slack.poll()
    assert m.target == f"{CH}/1790515000.000100"  # 답글은 스레드 첫 메시지 자리에
    assert [(c.author, c.text) for c in m.context] == [
        ("동료 김", "스레드 시작 질문"),
        ("ume", "이전 답"),
    ]
    assert ("conversations_replies", {"channel": CH, "ts": "1790515000.000100"}) in web.calls


def test_context_failure_keeps_mention(slack, web):
    web.errors["conversations_replies"] = api_error("missing_scope")
    deliver(slack, message(f"<@{ME}> 질문"))
    (m,) = slack.poll()
    assert m.context == []


def test_unknown_user_name_falls_back_to_id(slack, web):
    web.errors["users_info"] = api_error("user_not_found")
    deliver(slack, message(f"<@{ME}> 질문"))
    assert slack.poll()[0].author == FRIEND


# ---- 게시 --------------------------------------------------------------------------------


def test_post_replies_in_thread_as_me(web):
    slack = SlackChannel(web, "xapp", socket_factory=FakeSocket)  # 결재 서버: start 없이 post 만
    url = slack.post(f"{CH}/1790515000.000100", "승인된 답")
    assert web.calls == [
        ("chat_postMessage", {"channel": CH, "thread_ts": "1790515000.000100", "text": "승인된 답"})
    ]
    assert url == message_url(CH, "1790600000.000200")


@pytest.mark.parametrize("error", [api_error("channel_not_found"), OSError("network down")])
def test_post_failure_is_channel_error(web, error):
    web.errors["chat_postMessage"] = error
    with pytest.raises(
        ChannelError, match="chat.postMessage C0TEST/1.2: (channel_not_found|OSError)"
    ):
        SlackChannel(web, "xapp").post(f"{CH}/1.2", "x")
