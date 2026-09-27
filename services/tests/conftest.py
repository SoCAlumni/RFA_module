import pytest
from svc_support import TEST_CLEARANCE_KEY


@pytest.fixture(autouse=True)
def clearance_key_env(monkeypatch):
    """review 앱은 서명 키 없이 뜨지 않는다. 테스트 전체에 고정 키를 준다."""
    monkeypatch.setenv("RFA_CLEARANCE_KEY", TEST_CLEARANCE_KEY)
