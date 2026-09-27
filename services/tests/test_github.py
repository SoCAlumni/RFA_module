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
    NotificationTracker,
    find_mentions,
    parse_target,
)
from fake_github import REPO, FakeGithub, comment, issue, notification

SINCE = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
ME = "zetwhite"


def just_now() -> str:
    """스캔은 최근 24시간만 본다. 실제 시계와 무관하게 통과하도록 '방금 전' 시각을 쓴다."""
    return (datetime.now(UTC) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def config(mode: str = "notifications", repos: tuple[str, ...] = (REPO,), login=None):
    return GithubConfig(token="t", repos=repos, mode=mode, login=login)


def channel(
    fake: FakeGithub,
    tmp_path,
    mode: str = "notifications",
    repos: tuple[str, ...] = (REPO,),
    login: str | None = ME,
    clock=None,
) -> GithubChannel:
    return GithubChannel(
        GithubClient("t", transport=fake.transport()),
        config(mode, repos, login),
        NotificationTracker(tmp_path / "notifications_seen.json"),
        MentionTracker(tmp_path / "mentions_seen.json"),
        clock=clock or Clock(),
    )


# ---- 설정 ----------------------------------------------------------------------


def test_parse_target():
    assert parse_target("team/rfa-test#34") == ("team/rfa-test", 34)


def test_config_defaults_to_mentions_mode_and_keeps_old_env_working():
    env = {"GITHUB_TOKEN": "t", "RFA_GITHUB_LOGIN": ME, "RFA_GITHUB_REPOS": "a/b, c/d"}
    cfg = GithubConfig.from_env(env)
    assert (cfg.mode, cfg.repos, cfg.login) == ("mentions", ("a/b", "c/d"), ME)


def test_config_mentions_mode_requires_repos_but_not_login():
    assert GithubConfig.from_env({"GITHUB_TOKEN": "t", "RFA_GITHUB_REPOS": "a/b"}).login is None
    with pytest.raises(RuntimeError, match="RFA_GITHUB_REPOS"):
        GithubConfig.from_env({"GITHUB_TOKEN": "t"})


def test_config_notifications_mode_needs_only_token():
    cfg = GithubConfig.from_env({"GITHUB_TOKEN": "t", "RFA_GITHUB_MODE": "notifications"})
    assert (cfg.mode, cfg.repos) == ("notifications", ())
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        GithubConfig.from_env({"RFA_GITHUB_MODE": "notifications"})
    with pytest.raises(RuntimeError, match="unknown RFA_GITHUB_MODE"):
        GithubConfig.from_env({"GITHUB_TOKEN": "t", "RFA_GITHUB_MODE": "noti"})


def test_start_uses_env_login_or_asks_github(tmp_path):
    fake = FakeGithub()
    with_login = channel(fake, tmp_path, login=ME)
    with_login.start()
    assert with_login.login == ME and fake.paths("/user") == []

    auto = channel(fake, tmp_path / "b", login=None)
    auto.start()
    assert auto.login == ME and len(fake.paths("/user")) == 1


# ---- notifications 모드: 알림 → 멘션 ----------------------------------------------


def test_comment_notification_becomes_mention(tmp_path):
    fake = FakeGithub(
        issues=[issue(7, "본문")],
        comments=[comment(10, 7, "ORBIT 진행 어때요?", author="outside-dev")],
        notifications=[notification("n1", 7, reason="mention", cid=10)],
    )
    ch = channel(fake, tmp_path)

    (m,) = ch.poll()
    assert (m.channel, m.target, m.author) == ("github", f"{REPO}#7", "outside-dev")
    assert m.text == "ORBIT 진행 어때요?"
    assert str(m.url).endswith("#issuecomment-10")
    assert m.created_at == datetime(2026, 9, 26, 10, 5, tzinfo=UTC)
    # 요청에 participating=true 와 since 가 실린다
    (req,) = fake.paths("/notifications")
    assert req.url.params["participating"] == "true" and "since" in req.url.params


def test_issue_body_notification_uses_title_and_body(tmp_path):
    fake = FakeGithub(
        issues=[issue(8, "리뷰 부탁해요", author="teammate")],
        notifications=[notification("n1", 8, reason="review_requested", cid=None)],
    )
    (m,) = channel(fake, tmp_path).poll()
    assert (m.target, m.author, m.text) == (f"{REPO}#8", "teammate", "issue 8\n리뷰 부탁해요")


@pytest.mark.parametrize(
    "reason, picked",
    [
        ("mention", True),
        ("author", True),
        ("assign", True),
        ("team_mention", True),
        ("subscribed", False),
        ("ci_activity", False),
        ("state_change", False),
        ("security_alert", False),
    ],
)
def test_reason_allowlist(tmp_path, reason, picked):
    fake = FakeGithub(
        comments=[comment(10, 7, "글", author="other")],
        notifications=[notification("n1", 7, reason=reason, cid=10)],
    )
    assert bool(channel(fake, tmp_path).poll()) is picked


def test_non_issue_subject_is_skipped_and_pr_is_accepted(tmp_path):
    fake = FakeGithub(
        comments=[comment(10, 7, "PR 봐주세요", author="other")],
        notifications=[
            notification("n1", 3, reason="mention", type_="Discussion"),
            notification("n2", 7, reason="mention", cid=10, type_="PullRequest"),
        ],
    )
    (m,) = channel(fake, tmp_path).poll()
    assert m.target == f"{REPO}#7"


def test_same_thread_again_only_when_updated(tmp_path):
    fake = FakeGithub(
        comments=[comment(10, 7, "첫 질문", author="other")],
        notifications=[notification("n1", 7, cid=10, updated="2026-09-26T10:05:00Z")],
    )
    clock = Clock()
    ch = channel(fake, tmp_path, clock=clock)
    assert len(ch.poll()) == 1

    clock.now += 60  # 같은 updated_at → 스킵
    assert ch.poll() == []

    fake.comments.append(comment(11, 7, "추가 질문", author="other"))
    fake.notifications = [notification("n1", 7, cid=11, updated="2026-09-26T10:09:00Z")]
    clock.now += 60
    (m,) = ch.poll()
    assert m.text == "추가 질문"


def test_bot_reply_and_my_own_comment_are_skipped(tmp_path):
    fake = FakeGithub(
        comments=[
            comment(10, 7, f"승인된 답\n\n{BOT_MARKER}", author=ME),
            comment(11, 8, "내가 쓴 글", author=ME),
        ],
        issues=[issue(7, "x"), issue(8, "y")],
        notifications=[
            notification("n1", 7, reason="comment", cid=10),
            notification("n2", 8, reason="comment", cid=11),
        ],
    )
    assert channel(fake, tmp_path).poll() == []


def test_poll_interval_throttles_api_calls(tmp_path):
    fake = FakeGithub(poll_interval=60)
    clock = Clock()
    ch = channel(fake, tmp_path, clock=clock)

    assert ch.poll() == [] and len(fake.paths("/notifications")) == 1
    clock.now += 30
    assert ch.poll() == [] and len(fake.paths("/notifications")) == 1  # 간격 안 → 호출 없음
    clock.now += 31
    ch.poll()
    assert len(fake.paths("/notifications")) == 2


def test_repos_filter_limits_notifications(tmp_path):
    fake = FakeGithub(
        comments=[comment(10, 7, "우리 레포 질문", author="other")],
        notifications=[
            notification("n1", 7, cid=10),
            notification("n2", 9, cid=99, repo="other/repo"),  # 필터로 걸러져 fetch 안 함
        ],
    )
    (m,) = channel(fake, tmp_path, repos=(REPO,)).poll()
    assert m.target == f"{REPO}#7"


def test_notifications_pagination(tmp_path):
    comments = [comment(100 + i, 7, f"q{i}", author="other") for i in range(51)]
    notifications = [
        notification(f"n{i}", 7, cid=100 + i, updated=f"2026-09-26T10:05:{i % 60:02d}Z")
        for i in range(51)
    ]
    fake = FakeGithub(issues=[issue(7, "x")], comments=comments, notifications=notifications)
    tracker = NotificationTracker(tmp_path / "seen.json")
    found, interval = tracker.poll(GithubClient("t", transport=fake.transport()), (), ME)
    assert len(found) == 51 and interval == 60
    assert [r.url.params["page"] for r in fake.paths("/notifications")] == ["1", "2"]


def test_notification_and_self_mention_arrive_together(tmp_path):
    fake = FakeGithub(
        issues=[issue(7, "본문")],
        comments=[
            comment(10, 7, "남이 보낸 질문", author="other", created=just_now()),
            comment(11, 7, f"@{ME} 내가 남긴 메모", author=ME, created=just_now()),
        ],
        notifications=[notification("n1", 7, cid=10, updated=just_now())],
    )
    found = channel(fake, tmp_path).poll()
    assert [(m.author, m.text) for m in found] == [
        ("other", "남이 보낸 질문"),
        (ME, f"@{ME} 내가 남긴 메모"),
    ]


def test_no_repos_means_notifications_only(tmp_path):
    fake = FakeGithub(notifications=[])
    ch = channel(fake, tmp_path, repos=())
    assert ch.poll() == []
    assert fake.paths(f"/repos/{REPO}/issues") == []  # 스캔 안 함


# ---- 셀프 멘션 스캔 (notifications 모드) --------------------------------------------


def test_self_scan_takes_only_my_posts(tmp_path):
    fake = FakeGithub(
        issues=[issue(4, f"@{ME} 비서야 정리해줘", author=ME, created=just_now())],
        comments=[
            comment(20, 4, f"@{ME} 남이 쓴 멘션", author="other", created=just_now()),
            comment(21, 4, f"(담당: @{ME}) 답글\n\n{BOT_MARKER}", author=ME, created=just_now()),
        ],
    )
    (m,) = channel(fake, tmp_path).poll()  # 알림 없음 → 셀프 스캔만
    assert (m.author, m.target) == (ME, f"{REPO}#4")


# ---- mentions 모드 (기존 동작 보존) --------------------------------------------------


def test_find_mentions_rules():
    issues = [
        issue(1, f"@{ME} 이슈 본문에서 부름"),
        issue(2, f"@{ME}x 는 다른 사람"),
        issue(3, "@ZetWhite 대소문자 무시", created="2026-09-26T08:00:00Z"),  # since 이전
    ]
    comments = [
        comment(10, 7, f"hi @{ME}, ORBIT 진행 어때?"),
        comment(11, 7, f"email@{ME}.com 은 멘션 아님"),
        comment(12, 8, "cc @ZETWHITE."),
    ]
    found = find_mentions(ME, REPO, issues, comments, SINCE)
    assert [(m.target, m.author) for m in found] == [
        (f"{REPO}#1", "someone"),
        (f"{REPO}#7", "someone"),
        (f"{REPO}#8", "someone"),
    ]
    assert found[0].channel == "github" and found[0].audience == "public"


def test_mentions_mode_accepts_self_and_others_but_not_bot_reply():
    """기존 규칙: 본인이 쓴 @나 인정(혼자 데모), 비서가 단 답글은 무시."""
    issues = [issue(4, f"@{ME} 비서야 정리해줘", author=ME)]
    comments = [comment(20, 4, f"(담당: @{ME}) 검토 중.\n\n{BOT_MARKER}", author=ME)]
    found = find_mentions(ME, REPO, issues, comments, SINCE)
    assert [(m.target, m.author) for m in found] == [(f"{REPO}#4", ME)]


def test_mention_tracker_returns_each_once_and_advances_since(tmp_path):
    fake = FakeGithub(comments=[comment(10, 7, f"@{ME} 질문")])
    client = GithubClient("t", transport=fake.transport())
    tracker = MentionTracker(tmp_path / "mentions_seen.json")

    first = tracker.poll(client, (REPO,), ME, since=SINCE)
    second = tracker.poll(client, (REPO,), ME)

    assert [m.target for m in first] == [f"{REPO}#7"] and second == []
    saved = json.loads((tmp_path / "mentions_seen.json").read_text())
    assert saved["seen"] == [str(first[0].url)]
    since_params = [r.url.params.get("since") for r in fake.requests]
    assert since_params[0] == "2026-09-26T09:00:00Z" and since_params[2] != since_params[0]


def test_mentions_mode_polls_every_time_without_throttle(tmp_path):
    fake = FakeGithub(comments=[comment(10, 7, f"@{ME} 질문", created=just_now())])
    ch = channel(fake, tmp_path, mode="mentions")
    assert len(ch.poll()) == 1
    ch.poll()
    assert len(fake.paths(f"/repos/{REPO}/issues")) == 2  # 매 poll 마다 스캔
    assert fake.paths("/notifications") == []  # 알림 API 는 안 씀


def test_mentions_mode_poll_attaches_context_and_auto_login(tmp_path):
    fake = FakeGithub(
        issues=[issue(7, "ORBIT 결과 언제 나오나요?", author="opener")],
        comments=[comment(10, 7, f"@{ME} 답 부탁해요", "helper", just_now())],
    )
    ch = channel(fake, tmp_path, mode="mentions", login=None)  # LOGIN env 없이 자동 감지
    (m,) = ch.poll()
    assert (m.author, len(fake.paths("/user"))) == ("helper", 1)
    assert [(c.author, c.text) for c in m.context] == [
        ("opener", "issue 7\nORBIT 결과 언제 나오나요?")
    ]


def test_poll_keeps_mention_when_context_fails(tmp_path):
    # 이슈 #8 이 없어 맥락 조회가 404
    fake = FakeGithub(comments=[comment(10, 8, f"@{ME} 질문", created=just_now())])
    (m,) = channel(fake, tmp_path, mode="mentions").poll()
    assert m.target == f"{REPO}#8" and m.context == []


# ---- 공통: 클라이언트·게시 ------------------------------------------------------------


def test_client_sends_auth_and_api_headers():
    fake = FakeGithub()
    GithubClient("gh-token", transport=fake.transport()).issues_since(REPO, SINCE)
    req = fake.requests[0]
    assert req.headers["authorization"] == "Bearer gh-token"
    assert req.headers["x-github-api-version"] == "2022-11-28"


def test_thread_keeps_last_ten_and_excludes_the_mention():
    fake = FakeGithub(
        issues=[issue(7, "본문", author="opener")],
        comments=[comment(i, 7, f"c{i}", created=f"2026-09-26T10:{i:02d}:00Z") for i in range(12)],
    )
    client = GithubClient("t", transport=fake.transport())
    mention_url = f"https://github.com/{REPO}/issues/7#issuecomment-11"
    msgs = client.thread(f"{REPO}#7", exclude_url=mention_url)
    assert [m.text for m in msgs] == [f"c{i}" for i in range(1, 11)]  # c11(멘션 자신) 제외


def test_thread_cleans_bot_marker():
    fake = FakeGithub(
        issues=[issue(7, "질문", author="opener")],
        comments=[comment(1, 7, f"이미 단 답\n\n{BOT_MARKER}", author=ME)],
    )
    msgs = GithubClient("t", transport=fake.transport()).thread(f"{REPO}#7", exclude_url="x")
    assert [(m.author, m.text) for m in msgs] == [("opener", "issue 7\n질문"), (ME, "이미 단 답")]


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
        client.notifications(SINCE)


def test_channel_post_comments_with_marker(tmp_path):
    fake = FakeGithub()
    url = channel(fake, tmp_path).post(f"{REPO}#7", "승인된 답")
    assert url.endswith("#issuecomment-999")
    assert fake.posted == [(f"{REPO}#7", f"승인된 답\n\n{BOT_MARKER}")]


def test_create_comment_failure_raises():
    fake = FakeGithub(fail_post=403)
    with pytest.raises(GithubError, match="403"):
        GithubClient("t", transport=fake.transport()).create_comment(f"{REPO}#7", "x")


def test_channel_from_env(tmp_path):
    env = {"GITHUB_TOKEN": "t", "RFA_GITHUB_MODE": "notifications"}
    ch = GithubChannel.from_env(env, tmp_path)
    assert ch.config.mode == "notifications"
    assert ch.notifications._path == tmp_path / "notifications_seen.json"
    assert ch.mentions._path == tmp_path / "mentions_seen.json"


def test_posted_reply_is_not_picked_up_again_in_either_mode(tmp_path):
    """게시 → 다음 폴링: 우리가 단 답글이 새 멘션으로 돌아오지 않는다 (마커)."""
    fake = FakeGithub(issues=[issue(7, "x")])
    ch = channel(fake, tmp_path, mode="mentions")
    ch.post(f"{REPO}#7", f"(담당: @{ME}) 검토 중이에요.")
    ((_, posted_body),) = fake.posted
    fake.comments = [comment(30, 7, posted_body, author=ME, created=just_now())]

    assert ch.poll() == []
    noti = channel(fake, tmp_path / "n", mode="notifications")
    fake.notifications = [notification("n1", 7, reason="comment", cid=30, updated=just_now())]
    assert noti.poll() == []
