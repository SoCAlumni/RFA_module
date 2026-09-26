import pytest
from wf_support import WF_CLEARANCE_KEY, Env, make_env


@pytest.fixture(autouse=True)
def _clearance_key(monkeypatch):
    monkeypatch.setenv("RFA_CLEARANCE_KEY", WF_CLEARANCE_KEY)


@pytest.fixture
def env(tmp_path) -> Env:
    return make_env(tmp_path)
