import pytest
from wf_support import Env, make_env


@pytest.fixture
def env(tmp_path) -> Env:
    return make_env(tmp_path)
