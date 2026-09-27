import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from channels.base import ChannelError
from channels.github import (
    BOT_MARKER,
    GithubChannel,
    GithubClient,
    GithubConfig,
    GithubError,
    MentionTracker,
    find_mentions,
    parse_target,
)
from fake_github import REPO, FakeGithub, comment, issue

SINCE = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
CONFIG = GithubConfig(token="gh-token", login="zetwhite", repos=(REPO,))


def test_parse_target():
    assert parse_target("team/rfa-test#34") == ("team/rfa-test", 34)


def test_config_from_env_requires_keys():
    env = {"GITHUB_TOKEN": "t", "RFA_GITHUB_LOGIN": "zetwhite", "RFA_GITHUB_REPOS": "a/b, c/d"}
    assert GithubConfig.from_env(env).repos == ("a/b", "c/d")
    with pytest.raises(RuntimeError, match="RFA_GITHUB_REPOS"):
        GithubConfig.from_env({k: v for k, v in env.items() if k != "RFA_GITHUB_REPOS"})


def test_find_mentions_rules():
    issues = [
        issue(1, "@zetwhite 이슈 본문에서 부름"),
        issue(2, "@zetwhitex 는 다른 사람"),
        issue(3, "@ZetWhite 대소문자 무시", created="2026-09-26T08:00:00Z"),  # since 이전
    ]
    comments = [
        comment(10, 7, "hi @zetwhite, ORBIT 진행 어때?"),
        comment(11, 7, "email@zetwhite.com 은 멘션 아님"),
        comment(12, 8, "cc @ZETWHITE."),
    ]
    found = find_mentions("zetwhite", REPO, issues, comments, SINCE)
    assert [(m.target, m.author) for m in found] == [
        (f"{REPO}#1", "someone"),
        (f"{REPO}#7", "someone"),
        (f"{REPO}#8", "someone"),
    ]
    assert found[1].text == "hi @zetwhite, ORBIT 진행 어때?"
    assert str(found[1].url).endswith("#issuecomment-10")
    assert found[0].channel == "github"
    assert found[0].audience == "public"


def test_self_mention_counts_but_own_bot_reply_does_not():
    """본인이 쓴 @login 은 인정(혼자 데모 가능), 이 시스템이 단 답글은 무시(자기 반응 방지)."""
    issues = [issue(4, "@zetwhite 비서야 ORBIT 정리해줘", author="zetwhite")]
    comments = [
        comment(20, 4, f"(담당: @zetwhite) 검토 중이에요.\n\n{BOT_MARKER}", author="zetwhite"),
    ]
    found = find_mentions("zetwhite", REPO, issues, comments, SINCE)
    assert [(m.target, m.author) for m in found] == [(f"{REPO}#4", "zetwhite")]


def test_tracker_returns_each_mention_once_and_advances_since(tmp_path):
    fake = FakeGithub(comments=[comment(10, 7, "@zetwhite 질문")])
    client = GithubClient("gh-token", transport=fake.transport())
    tracker = MentionTracker(tmp_path / "mentions_seen.json")

    first = tracker.poll(client, CONFIG, SINCE)
    second = tracker.poll(client, CONFIG)

    assert [m.target for m in first] == [f"{REPO}#7"]
    assert second == []
    saved = json.loads((tmp_path / "mentions_seen.json").read_text())
    assert saved["seen"] == [str(first[0].url)]
    # 두 번째 호출은 저장된 since 를 쓴다
    since_params = [r.url.params.get("since") for r in fake.requests]
    assert since_params[0] == "2026-09-26T09:00:00Z"
    assert since_params[2] != since_params[0]


def test_client_sends_auth_and_api_headers():
    fake = FakeGithub()
    GithubClient("gh-token", transport=fake.transport()).issues_since(REPO, SINCE)
    req = fake.requests[0]
    assert req.headers["authorization"] == "Bearer gh-token"
    assert req.headers["x-github-api-version"] == "2022-11-28"
    assert req.url.params["state"] == "all"


def test_thread_keeps_last_ten_and_excludes_the_mention():
    fake = FakeGithub(
        issues=[issue(7, "본문", author="opener")],
        comments=[comment(i, 7, f"c{i}", created=f"2026-09-26T10:{i:02d}:00Z") for i in range(12)],
    )
    client = GithubClient("t", transport=fake.transport())
    mention_url = f"https://github.com/{REPO}/issues/7#issuecomment-11"

    msgs = client.thread(f"{REPO}#7", exclude_url=mention_url)

    assert [m.text for m in msgs] == [f"c{i}" for i in range(1, 11)]  # c11(멘션 자신) 제외


def test_thread_includes_issue_title_and_body_and_cleans_bot_marker():
    fake = FakeGithub(
        issues=[issue(7, "@zetwhite 본문 질문", author="opener")],
        comments=[comment(1, 7, f"이미 단 답\n\n{BOT_MARKER}", author="zetwhite")],
    )
    client = GithubClient("t", transport=fake.transport())

    msgs = client.thread(f"{REPO}#7", exclude_url="https://example.com/other")
    assert [(m.author, m.text) for m in msgs] == [
        ("opener", "issue 7\n@zetwhite 본문 질문"),
        ("zetwhite", "이미 단 답"),
    ]
    # 멘션이 이슈 본문이면 본문을 뺀다
    only = client.thread(f"{REPO}#7", exclude_url=f"https://github.com/{REPO}/issues/7")
    assert [m.author for m in only] == ["zetwhite"]


def test_api_error_raises_channel_error():
    fake = FakeGithub()
    with pytest.raises(GithubError, match="404") as info:
        GithubClient("t", transport=fake.transport()).thread(f"{REPO}#99", exclude_url="")
    assert isinstance(info.value, ChannelError)


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no route")


def test_network_error_becomes_github_error():
    client = GithubClient("t", transport=httpx.MockTransport(_unreachable))
    with pytest.raises(GithubError, match="POST .* ConnectError"):
        client.create_comment(f"{REPO}#7", "x")
    with pytest.raises(GithubError, match="GET .* ConnectError"):
        client.issues_since(REPO, SINCE)


def test_create_comment_marks_body_and_returns_url():
    fake = FakeGithub()
    url = GithubClient("t", transport=fake.transport()).create_comment(f"{REPO}#7", "승인된 답변")
    assert url.endswith("#issuecomment-999")
    assert fake.posted == [(f"{REPO}#7", f"승인된 답변\n\n{BOT_MARKER}")]


def test_create_comment_failure_raises():
    fake = FakeGithub(fail_post=403)
    with pytest.raises(GithubError, match="403"):
        GithubClient("t", transport=fake.transport()).create_comment(f"{REPO}#7", "x")


def test_posted_reply_is_not_picked_up_as_new_mention(tmp_path):
    """게시 → 다음 폴링: 우리가 단 답글(@login 포함)이 새 멘션으로 돌아오지 않는다."""
    fake = FakeGithub()
    client = GithubClient("t", transport=fake.transport())
    client.create_comment(f"{REPO}#7", "(담당: @zetwhite) 보완 검토 중이에요.")
    ((_, posted_body),) = fake.posted
    fake.comments = [comment(30, 7, posted_body, author="zetwhite")]

    assert MentionTracker(tmp_path / "seen.json").poll(client, CONFIG, SINCE) == []


# ---- GithubChannel -------------------------------------------------------------


def just_now() -> str:
    """poll 은 최근 24시간만 본다. 실제 시계와 무관하게 통과하도록 '방금 전' 시각을 쓴다."""
    return (datetime.now(UTC) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def channel(fake: FakeGithub, tmp_path) -> GithubChannel:
    client = GithubClient("t", transport=fake.transport())
    return GithubChannel(client, CONFIG, MentionTracker(tmp_path / "seen.json"))


def test_channel_poll_attaches_thread_context(tmp_path):
    fake = FakeGithub(
        issues=[issue(7, "ORBIT 벤치마크 결과 언제 나오나요?", author="opener")],
        comments=[comment(10, 7, "@zetwhite 여기 답 좀 부탁해요", "helper", just_now())],
    )
    ch = channel(fake, tmp_path)
    assert ch.kind == "github"

    (m,) = ch.poll()
    assert (m.target, m.author) == (f"{REPO}#7", "helper")
    assert [(c.author, c.text) for c in m.context] == [
        ("opener", "issue 7\nORBIT 벤치마크 결과 언제 나오나요?")
    ]
    assert ch.poll() == []  # 한 번 돌려준 멘션은 다시 안 나옴


def test_channel_poll_keeps_mention_when_context_fails(tmp_path):
    # 이슈 #8 이 없어 맥락 조회가 404
    fake = FakeGithub(comments=[comment(10, 8, "@zetwhite 질문", created=just_now())])
    (m,) = channel(fake, tmp_path).poll()
    assert m.target == f"{REPO}#8" and m.context == []


def test_channel_post_comments_with_marker(tmp_path):
    fake = FakeGithub()
    url = channel(fake, tmp_path).post(f"{REPO}#7", "승인된 답")
    assert url.endswith("#issuecomment-999")
    assert fake.posted == [(f"{REPO}#7", f"승인된 답\n\n{BOT_MARKER}")]


def test_channel_from_env(tmp_path):
    env = {"GITHUB_TOKEN": "t", "RFA_GITHUB_LOGIN": "zetwhite", "RFA_GITHUB_REPOS": REPO}
    ch = GithubChannel.from_env(env, tmp_path)
    assert ch.config.repos == (REPO,)
    assert ch.tracker._path == tmp_path / "mentions_seen.json"
