"""승인 토큰(clearance) 서명과 검증.

token = base64url(payload json) + "." + base64url(HMAC-SHA256(key, payload))
payload = {"review_id", "target", "body_sha256", "exp"}

- 승인된 그 본문(body)만 게시할 수 있다: 검증 시 sha256(body) 일치를 요구.
- 게시하는 쪽(publisher)은 review 상태를 몰라도 이 토큰만으로 승인 여부를 확인한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

DEFAULT_TTL_SECONDS = 600


class ClearanceError(Exception):
    """토큰이 없거나, 위조됐거나, 만료됐거나, 다른 대상/본문에 대한 것."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.b64decode(text + pad, altchars=b"-_", validate=True)


def _sig(key: str, payload: bytes) -> bytes:
    return hmac.new(key.encode(), payload, hashlib.sha256).digest()


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def sign(
    key: str, review_id: int, target: str, body: str, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    payload = json.dumps(
        {
            "review_id": review_id,
            "target": target,
            "body_sha256": body_sha256(body),
            "exp": int(time.time()) + ttl_seconds,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"{_b64e(payload)}.{_b64e(_sig(key, payload))}"


def verify(key: str, token: str, target: str, body: str) -> dict:
    """통과하면 payload dict, 아니면 ClearanceError."""
    try:
        payload_b64, sig_b64 = token.split(".")
        payload_raw = _b64d(payload_b64)
        signature = _b64d(sig_b64)
    except ValueError as exc:
        raise ClearanceError("malformed") from exc
    if not hmac.compare_digest(signature, _sig(key, payload_raw)):
        raise ClearanceError("bad_signature")
    payload = json.loads(payload_raw)
    if payload["exp"] < time.time():
        raise ClearanceError("expired")
    if payload["target"] != target:
        raise ClearanceError("target_mismatch")
    if payload["body_sha256"] != body_sha256(body):
        raise ClearanceError("body_mismatch")
    return payload
