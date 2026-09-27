import pytest
from channels.github import GithubChannel
from channels.registry import make_channels
from channels.slack import SlackChannel
from fake_github import REPO

GITHUB_ENV = {"GITHUB_TOKEN": "t", "RFA_GITHUB_LOGIN": "zetwhite", "RFA_GITHUB_REPOS": REPO}


@pytest.mark.parametrize("value", [None, "", " , "])
def test_no_channels_by_default(value):
    env = {} if value is None else {"RFA_CHANNELS": value}
    assert make_channels(env) == {}


def test_github_channel_uses_data_dir(tmp_path):
    env = {**GITHUB_ENV, "RFA_CHANNELS": " github ", "RFA_DATA_DIR": str(tmp_path)}
    channels = make_channels(env)
    assert list(channels) == ["github"]
    assert isinstance(channels["github"], GithubChannel)
    assert channels["github"].config.mode == "mentions"  # 기본값: 기존 동작
    assert channels["github"].mentions._path == tmp_path / "state" / "mentions_seen.json"
    assert channels["github"].notifications._path == tmp_path / "state" / "notifications_seen.json"


def test_channel_missing_env_fails_loudly():
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        make_channels({"RFA_CHANNELS": "github"})
    # mentions 모드(기본)는 기존처럼 레포 목록이 필수
    with pytest.raises(RuntimeError, match="RFA_GITHUB_REPOS"):
        make_channels({"RFA_CHANNELS": "github", "GITHUB_TOKEN": "t"})


def test_github_notifications_mode_needs_only_token(tmp_path):
    env = {
        "RFA_CHANNELS": "github",
        "GITHUB_TOKEN": "t",
        "RFA_GITHUB_MODE": "notifications",
        "RFA_DATA_DIR": str(tmp_path),
    }
    channels = make_channels(env)
    assert channels["github"].config.mode == "notifications"


def test_github_and_slack_together():
    env = {
        **GITHUB_ENV,
        "RFA_CHANNELS": "github,slack",
        "SLACK_USER_TOKEN": "xoxp-x",
        "SLACK_APP_TOKEN": "xapp-x",
    }
    channels = make_channels(env)
    assert list(channels) == ["github", "slack"]
    assert isinstance(channels["slack"], SlackChannel)


def test_slack_missing_env_fails_loudly():
    with pytest.raises(RuntimeError, match="SLACK_USER_TOKEN, SLACK_APP_TOKEN"):
        make_channels({"RFA_CHANNELS": "slack"})


def test_unknown_channel():
    with pytest.raises(RuntimeError, match="unknown RFA_CHANNELS entry"):
        make_channels({"RFA_CHANNELS": "email"})
