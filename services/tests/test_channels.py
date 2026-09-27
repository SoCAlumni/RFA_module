import pytest
from channels.github import GithubChannel
from channels.registry import make_channels
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
    assert channels["github"].tracker._path == tmp_path / "state" / "mentions_seen.json"


def test_channel_missing_env_fails_loudly():
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        make_channels({"RFA_CHANNELS": "github"})


@pytest.mark.parametrize("name", ["slack", "email"])
def test_unknown_or_not_yet_built_channel(name):
    with pytest.raises(RuntimeError, match="unknown RFA_CHANNELS entry"):
        make_channels({"RFA_CHANNELS": name})
