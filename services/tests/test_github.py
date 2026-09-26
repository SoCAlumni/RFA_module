import json
from datetime import UTC, datetime

import pytest
from fake_github import REPO, FakeGithub, comment, issue
from fastapi.testclient import TestClient
from mcp_channels.github import (
    BOT_MARKER,
    GithubClient,
    GithubConfig,
    GithubError,
    MentionTracker,
    find_mentions,
)
from mcp_channels.server import MCP_PATH, create_app
from review.app import create_app as create_review_app
from review.clearance import sign
from review.publisher import GithubPublisher, MockPublisher, PublishError, make_publisher
from test_approval import LOOPBACK, to_reviewed
from test_review_core import OPEN, REDACT

SINCE = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
CONFIG = GithubConfig(token="gh-token", login="zetwhite", repos=(REPO,))


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
    assert found[0].channel == "public"


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


def test_get_thread_keeps_last_ten_comments():
    fake = FakeGithub(
        issues=[issue(7, "본문")],
        comments=[comment(i, 7, f"c{i}", created=f"2026-09-26T10:{i:02d}:00Z") for i in range(12)],
    )
    thread = GithubClient("t", transport=fake.transport()).get_thread(f"{REPO}#7")
    assert (thread.title, thread.body, thread.author) == ("issue 7", "본문", "someone")
    assert [c.body for c in thread.comments] == [f"c{i}" for i in range(2, 12)]


def test_api_error_raises():
    fake = FakeGithub()
    with pytest.raises(GithubError, match="404"):
        GithubClient("t", transport=fake.transport()).get_thread(f"{REPO}#99")


def test_github_publisher_verifies_clearance_before_calling_api():
    fake = FakeGithub()
    pub = GithubPublisher(clearance_key="k", client=GithubClient("t", transport=fake.transport()))
    target, body = f"{REPO}#7", "승인된 답변"

    with pytest.raises(PublishError, match="body_mismatch"):
        pub.publish(target, body + "!", sign("k", 1, target, body))
    with pytest.raises(PublishError, match="bad_signature"):
        pub.publish(target, body, sign("other", 1, target, body))
    assert fake.requests == []  # 검증 실패면 GitHub 호출 없음

    url = pub.publish(target, body, sign("k", 1, target, body))
    assert url.endswith("#issuecomment-999")
    assert fake.posted == [(target, f"{body}\n\n{BOT_MARKER}")]


def test_posted_reply_is_not_picked_up_as_new_mention(tmp_path):
    """게시 → 다음 폴링: 우리가 단 답글(@login 포함)이 새 멘션으로 돌아오지 않는다."""
    fake = FakeGithub()
    client = GithubClient("t", transport=fake.transport())
    target = f"{REPO}#7"
    reply = "(담당: @zetwhite) 보완 검토 중이에요."
    GithubPublisher(clearance_key="k", client=client).publish(
        target, reply, sign("k", 1, target, reply)
    )
    ((_, posted_body),) = fake.posted
    fake.comments = [comment(30, 7, posted_body, author="zetwhite")]

    assert MentionTracker(tmp_path / "seen.json").poll(client, CONFIG, SINCE) == []


def test_github_publisher_wraps_api_failure():
    fake = FakeGithub(fail_post=403)
    pub = GithubPublisher(clearance_key="k", client=GithubClient("t", transport=fake.transport()))
    with pytest.raises(PublishError, match="403"):
        pub.publish(f"{REPO}#7", "x", sign("k", 1, f"{REPO}#7", "x"))


def test_make_publisher_selects_by_env():
    assert isinstance(make_publisher({}, "k"), MockPublisher)
    assert isinstance(
        make_publisher({"RFA_PUBLISHER": "github", "GITHUB_TOKEN": "t"}, "k"), GithubPublisher
    )
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        make_publisher({"RFA_PUBLISHER": "github"}, "k")
    with pytest.raises(RuntimeError, match="unknown"):
        make_publisher({"RFA_PUBLISHER": "slack"}, "k")


# ---------- MCP 서버 ----------

ENV = {
    "GITHUB_TOKEN": "gh-token",
    "RFA_GITHUB_LOGIN": "zetwhite",
    "RFA_GITHUB_REPOS": REPO,
    "GITHUB_MCP_TOKEN": "mcp-secret",
}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture
def mcp_client(tmp_path):
    fake = FakeGithub(issues=[issue(7, "본문")], comments=[comment(10, 7, "@zetwhite 질문")])
    app = create_app(
        {**ENV, "RFA_DATA_DIR": str(tmp_path)}, GithubClient("t", transport=fake.transport())
    )
    with TestClient(app, base_url="http://127.0.0.1:8792") as client:
        yield client


def rpc(client, method, params=None, token="mcp-secret"):
    headers = {**MCP_HEADERS, "authorization": f"Bearer {token}"}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(MCP_PATH, headers=headers, json=body)


def test_mcp_requires_bearer(mcp_client):
    assert rpc(mcp_client, "tools/list", token="wrong").status_code == 401
    res = mcp_client.post(MCP_PATH, headers=MCP_HEADERS, json={})
    assert res.status_code == 401
    assert res.json() == {"error": "unauthorized"}


def test_mcp_lists_only_read_tools(mcp_client):
    rpc(
        mcp_client,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    )
    tools = rpc(mcp_client, "tools/list").json()["result"]["tools"]
    assert sorted(t["name"] for t in tools) == ["get_thread", "list_mentions"]
    assert all(t["annotations"]["readOnlyHint"] for t in tools)


def test_mcp_call_list_mentions(mcp_client):
    res = rpc(
        mcp_client,
        "tools/call",
        {"name": "list_mentions", "arguments": {"since": "2026-09-26T09:00:00Z"}},
    )
    result = res.json()["result"]
    assert not result.get("isError")
    mentions = result["structuredContent"]["result"]
    assert [m["target"] for m in mentions] == [f"{REPO}#7"]


def test_mcp_rejects_unknown_host(tmp_path):
    fake = FakeGithub()
    app = create_app(
        {**ENV, "RFA_DATA_DIR": str(tmp_path)}, GithubClient("t", transport=fake.transport())
    )
    with TestClient(app, base_url="http://evil.example:8792") as client:
        assert rpc(client, "tools/list").status_code in (400, 421)


def test_server_requires_env():
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        create_app({})
    with pytest.raises(RuntimeError, match="GITHUB_MCP_TOKEN"):
        create_app({k: v for k, v in ENV.items() if k != "GITHUB_MCP_TOKEN"})


def test_approve_posts_final_body_to_github(tmp_path):
    fake = FakeGithub()
    publisher = GithubPublisher(
        clearance_key="review-key", client=GithubClient("t", transport=fake.transport())
    )
    app = create_review_app(tmp_path, clearance_key="review-key", publisher=publisher)
    human = TestClient(app, client=LOOPBACK)
    rid = to_reviewed(human)

    res = human.post(f"/reviews/{rid}/approve").json()

    assert res["status"] == "posted"
    assert res["posted_url"].endswith("#issuecomment-999")
    assert fake.posted == [(OPEN["target"], f"{REDACT['redacted_body']}\n\n{BOT_MARKER}")]
