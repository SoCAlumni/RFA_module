"""호스트 서비스 호출: head agent(/ask) 와 결재 서버(/approvals). 계약은 contracts/*.openapi.yaml.

일시적 오류(연결 실패·타임아웃, 429, 5xx)는 지수 backoff 로 최대 TRANSIENT_RETRIES 번 다시 시도한다.
그 밖의 오류(4xx)와 재시도 후에도 실패하면 ServiceError. 그래프는 이번 실행을 failed 로 끝낸다.

http 는 base_url 이 잡힌 httpx.Client 호환 객체. 테스트에서는 FastAPI TestClient 를 넣는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from rfa_common.contracts import (
    Approval,
    AskRequest,
    AskResponse,
    CreateApprovalRequest,
    ReviseApprovalRequest,
)

TIMEOUT = 120  # head agent 는 뒤에서 LLM 을 돌릴 수 있다
TRANSIENT_RETRIES = 3
BACKOFF_BASE = 0.5  # 0.5s, 1s, 2s
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


class ServiceError(Exception):
    """호스트 서비스 오류 (4xx, 또는 재시도 후에도 실패)."""


class _Service:
    def __init__(self, http: HttpLike, sleep: Callable[[float], None] = time.sleep) -> None:
        self._http = http
        self._sleep = sleep

    @classmethod
    def from_url(cls, url: str):
        return cls(httpx.Client(base_url=url, timeout=TIMEOUT))

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        """요청하고 JSON 을 돌려준다. 일시적 오류는 다시 시도한다."""
        what = f"{method.upper()} {path}"
        for attempt in range(TRANSIENT_RETRIES + 1):
            try:
                res = getattr(self._http, method)(path, **kwargs)
            except httpx.TransportError as exc:  # 연결 실패, 타임아웃
                problem = type(exc).__name__
            else:
                if res.status_code not in TRANSIENT_STATUS:
                    if res.status_code >= 400:
                        raise ServiceError(f"{what}: {res.status_code} {res.text[:200]}")
                    return res.json()
                problem = f"HTTP {res.status_code}"
            if attempt == TRANSIENT_RETRIES:
                raise ServiceError(f"{what}: {problem} (재시도 {TRANSIENT_RETRIES}회 후)")
            self._sleep(BACKOFF_BASE * 2**attempt)
        raise AssertionError("unreachable")


class HeadClient(_Service):
    def ask(self, req: AskRequest) -> AskResponse:
        data = self._call("post", "/ask", json=req.model_dump(mode="json"))
        return AskResponse.model_validate(data)


class ApprovalsClient(_Service):
    def create(self, req: CreateApprovalRequest) -> Approval:
        """같은 멘션(source_url)의 안건이 이미 있으면 서버가 그 안건을 돌려준다."""
        data = self._call("post", "/approvals", json=req.model_dump(mode="json"))
        return Approval.model_validate(data)

    def revise(self, approval_id: int, req: ReviseApprovalRequest) -> Approval:
        path = f"/approvals/{approval_id}/revise"
        return Approval.model_validate(self._call("post", path, json=req.model_dump(mode="json")))

    def get(self, approval_id: int) -> Approval:
        return Approval.model_validate(self._call("get", f"/approvals/{approval_id}"))
