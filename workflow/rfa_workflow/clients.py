"""호스트 서비스 호출 (review, knowledge). 둘 다 OpenAPI 계약(contracts/*.openapi.yaml)을 따른다.

복구 규칙:
- 일시적 오류(연결·타임아웃, 429, 5xx): 지수 backoff 로 최대 TRANSIENT_RETRIES 번 재시도.
  재시도마다 복구 예산을 쓴다.
- 409(상태 충돌): 최신 문서를 조회한다.
    · 이미 사람이 처리한 문서(approved/posted/rejected/needs_human) → AlreadyHandled
    · 내 요청이 이미 반영됨(재시도 중 첫 요청이 실제로는 성공) → 성공으로 보고 계속
      (이미 성공한 것이므로 예산을 쓰지 않고 기록만 한다)
    · 그 밖의 충돌 → ReviewConflict (복구 불가)
- 문서 생성(POST /reviews)은 서버가 source_url 로 멱등 처리한다.

http 는 base_url 이 잡힌 httpx.Client 호환 객체. 테스트에서는 FastAPI TestClient 를 넣는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from rfa_common.models import (
    EditVerdict,
    KnowledgeResult,
    Mention,
    Policy,
    Review,
    ReviewStatus,
    TaskInfo,
    Verdict,
)

from rfa_workflow.recovery import RecoveryBudget

TIMEOUT = 30
TRANSIENT_RETRIES = 3
BACKOFF_BASE = 0.5  # 0.5s, 1s, 2s
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
HANDLED = frozenset(
    {ReviewStatus.APPROVED, ReviewStatus.POSTED, ReviewStatus.REJECTED, ReviewStatus.NEEDS_HUMAN}
)


class HttpLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


class ReviewConflict(Exception):
    """review 상태기계가 전이를 거부했고, 이미 반영된 것도 아니다 (복구 불가)."""


class AlreadyHandled(Exception):
    """문서가 이미 사람 손에 넘어갔거나 끝났다. 처리된 상태를 그대로 돌려준다."""

    def __init__(self, review: Review) -> None:
        super().__init__(f"review {review.id} 은(는) 이미 {review.status}")
        self.review = review


class ServiceError(Exception):
    """호스트 서비스 오류 (복구 불가한 4xx, 또는 재시도 후에도 실패)."""


def resolve_conflict(
    latest: Review, applied: Callable[[Review], bool], what: str, budget: RecoveryBudget
) -> Review:
    """409 를 받은 뒤 최신 문서를 보고 결정한다."""
    if latest.status in HANDLED:
        raise AlreadyHandled(latest)
    if applied(latest):
        budget.note(f"409 {what}: 이미 반영됨")  # 이미 성공한 요청 → 예산과 무관하게 성공
        return latest
    raise ReviewConflict(f"{what}: 문서가 {latest.status} 상태라 진행 불가")


class _Service:
    def __init__(
        self,
        http: HttpLike,
        budget: RecoveryBudget | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._budget = budget or RecoveryBudget()
        self._sleep = sleep

    def with_budget(self, budget: RecoveryBudget):
        return type(self)(self._http, budget, self._sleep)

    @classmethod
    def from_url(cls, url: str):
        return cls(httpx.Client(base_url=url, timeout=TIMEOUT))

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        what = f"{method.upper()} {path}"
        for attempt in range(TRANSIENT_RETRIES + 1):
            try:
                res = getattr(self._http, method)(path, **kwargs)
            except httpx.TransportError as exc:  # 연결 실패, 타임아웃
                problem = type(exc).__name__
            else:
                if res.status_code not in TRANSIENT_STATUS:
                    return res
                problem = f"HTTP {res.status_code}"
            if attempt == TRANSIENT_RETRIES:
                raise ServiceError(f"{what}: {problem} (재시도 {TRANSIENT_RETRIES}회 후)")
            self._budget.spend(f"재시도 {what}: {problem}")
            self._sleep(BACKOFF_BASE * 2**attempt)
        raise AssertionError("unreachable")

    def _json(self, res: Any, what: str) -> Any:
        if res.status_code >= 400:
            raise ServiceError(f"{what}: {res.status_code} {res.text[:200]}")
        return res.json()


class ReviewClient(_Service):
    def get(self, review_id: int) -> Review:
        path = f"/reviews/{review_id}"
        return Review.model_validate(self._json(self._request("get", path), f"GET {path}"))

    def _mutate(
        self, review_id: int, path: str, body: dict, actor: str, applied: Callable[[Review], bool]
    ) -> Review:
        res = self._request("post", path, json=body, headers={"X-RFA-Actor": actor})
        if res.status_code == 409:
            return resolve_conflict(self.get(review_id), applied, f"POST {path}", self._budget)
        return Review.model_validate(self._json(res, f"POST {path}"))

    def open(self, mention: Mention) -> Review:
        body = {
            "channel": mention.channel,
            "target": mention.target,
            "source_url": str(mention.url),
            "requester": mention.author,
            "question": mention.text,
        }
        res = self._request("post", "/reviews", json=body, headers={"X-RFA-Actor": "intake"})
        return Review.model_validate(self._json(res, "POST /reviews"))

    def attach_knowledge(self, review_id: int, knowledge: KnowledgeResult) -> Review:
        return self._mutate(
            review_id,
            f"/reviews/{review_id}/knowledge",
            knowledge.model_dump(mode="json"),
            "knowledge",
            lambda r: r.knowledge == knowledge,
        )

    def submit_draft(self, review_id: int, text: str, edit_log: list[EditVerdict]) -> Review:
        return self._mutate(
            review_id,
            f"/reviews/{review_id}/draft",
            {"text": text, "edit_log": [e.model_dump(mode="json") for e in edit_log]},
            "press",
            lambda r: r.draft == text and r.status == ReviewStatus.SCANNED,
        )

    def submit_verdict(self, review_id: int, verdict: Verdict) -> Review:
        return self._mutate(
            review_id,
            f"/reviews/{review_id}/verdict",
            verdict.model_dump(mode="json"),
            "censor_public",
            lambda r: r.verdict == verdict,
        )

    def needs_human(self, review_id: int, reason: str) -> Review:
        return self._mutate(
            review_id,
            f"/reviews/{review_id}/needs-human",
            {"reason": reason},
            "workflow",
            lambda r: False,
        )

    def policy(self, scope: str) -> Policy:
        path = f"/policy/{scope}"
        return Policy.model_validate(self._json(self._request("get", path), f"GET {path}"))


class KnowledgeClient(_Service):
    def tasks(self) -> list[TaskInfo]:
        data = self._json(self._request("get", "/tasks"), "GET /tasks")
        return [TaskInfo.model_validate(t) for t in data]

    def ask(self, task_id: str, question: str) -> KnowledgeResult:
        path = f"/tasks/{task_id}/ask"
        res = self._request("post", path, json={"question": question})
        return KnowledgeResult.model_validate(self._json(res, f"POST {path}"))
