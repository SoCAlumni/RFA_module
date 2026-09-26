import time

import pytest
from review.clearance import ClearanceError, sign, verify

KEY = "k1"
BODY = "양자화 후 정확도가 소폭 하락했어요."
TARGET = "zetwhite/rfa-test#34"


def test_sign_verify_roundtrip():
    token = sign(KEY, 12, TARGET, BODY)
    payload = verify(KEY, token, TARGET, BODY)
    assert payload["review_id"] == 12
    assert payload["target"] == TARGET
    assert payload["exp"] > time.time()


def reason_of(**kwargs) -> str:
    defaults = dict(key=KEY, token=sign(KEY, 12, TARGET, BODY), target=TARGET, body=BODY)
    defaults.update(kwargs)
    with pytest.raises(ClearanceError) as exc:
        verify(defaults["key"], defaults["token"], defaults["target"], defaults["body"])
    return exc.value.reason


def test_wrong_key_rejected():
    assert reason_of(key="other") == "bad_signature"


def test_tampered_payload_rejected():
    payload_b64, sig_b64 = sign(KEY, 12, TARGET, BODY).split(".")
    flipped = ("A" if payload_b64[0] != "A" else "B") + payload_b64[1:]
    assert reason_of(token=f"{flipped}.{sig_b64}") == "bad_signature"


def test_tampered_signature_rejected():
    payload_b64, sig_b64 = sign(KEY, 12, TARGET, BODY).split(".")
    flipped = ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
    assert reason_of(token=f"{payload_b64}.{flipped}") == "bad_signature"


@pytest.mark.parametrize("token", ["", "no-dot", "a.b.c", "@@bad@@.__"])
def test_malformed_token_rejected(token):
    assert reason_of(token=token) == "malformed"


def test_expired_rejected():
    token = sign(KEY, 12, TARGET, BODY, ttl_seconds=-1)
    assert reason_of(token=token) == "expired"


def test_target_mismatch_rejected():
    assert reason_of(target="zetwhite/rfa-test#35") == "target_mismatch"


def test_body_change_rejected():
    assert reason_of(body=BODY + " (수정)") == "body_mismatch"
