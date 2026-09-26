"""호스트 서비스 호출 (review, knowledge). 둘 다 OpenAPI 계약(contracts/*.openapi.yaml)을 따른다.

http 는 base_url 이 잡힌 httpx.Client 호환 객체. 테스트에서는 FastAPI TestClient 를 그대로 넣는다.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from rfa_common.models import (
    EditVerdict,
    KnowledgeResult,
    Mention,
    Policy,
    Review,
    TaskInfo,
    Verdict,
)

TIMEOUT = 30


class HttpLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> Any: ...
    def post(self, url: str, **kwargs: Any) -> Any: ...


class ReviewConflict(Exception):
    """review 상태기계가 전이를 거부(409). 다른 누군가 먼저 상태를 바꿨다는 뜻."""


class ServiceError(Exception):
    """호스트 서비스가 2xx/409 가 아닌 응답을 줬다."""


def _check(res: Any, what: str) -> Any:
    if res.status_code == 409:
        raise ReviewConflict(f"{what}: {res.json()}")
    if res.status_code >= 400:
        raise ServiceError(f"{what}: {res.status_code} {res.text[:200]}")
    return res.json()


class ReviewClient:
    def __init__(self, http: HttpLike) -> None:
        self._http = http

    @classmethod
    def from_url(cls, url: str) -> ReviewClient:
        return cls(httpx.Client(base_url=url, timeout=TIMEOUT))

    def _post(self, path: str, body: dict, actor: str) -> Review:
        res = self._http.post(path, json=body, headers={"X-RFA-Actor": actor})
        return Review.model_validate(_check(res, f"POST {path}"))

    def open(self, mention: Mention) -> Review:
        body = {
            "channel": mention.channel,
            "target": mention.target,
            "source_url": str(mention.url),
            "requester": mention.author,
            "question": mention.text,
        }
        return self._post("/reviews", body, "intake")

    def attach_knowledge(self, review_id: int, knowledge: KnowledgeResult) -> Review:
        return self._post(
            f"/reviews/{review_id}/knowledge", knowledge.model_dump(mode="json"), "knowledge"
        )

    def submit_draft(self, review_id: int, text: str, edit_log: list[EditVerdict]) -> Review:
        body = {"text": text, "edit_log": [e.model_dump(mode="json") for e in edit_log]}
        return self._post(f"/reviews/{review_id}/draft", body, "press")

    def submit_verdict(self, review_id: int, verdict: Verdict) -> Review:
        return self._post(
            f"/reviews/{review_id}/verdict", verdict.model_dump(mode="json"), "censor_public"
        )

    def needs_human(self, review_id: int, reason: str) -> Review:
        return self._post(f"/reviews/{review_id}/needs-human", {"reason": reason}, "workflow")

    def policy(self, scope: str) -> Policy:
        return Policy.model_validate(_check(self._http.get(f"/policy/{scope}"), "GET policy"))


class KnowledgeClient:
    def __init__(self, http: HttpLike) -> None:
        self._http = http

    @classmethod
    def from_url(cls, url: str) -> KnowledgeClient:
        return cls(httpx.Client(base_url=url, timeout=TIMEOUT))

    def tasks(self) -> list[TaskInfo]:
        return [TaskInfo.model_validate(t) for t in _check(self._http.get("/tasks"), "GET tasks")]

    def ask(self, task_id: str, question: str) -> KnowledgeResult:
        res = self._http.post(f"/tasks/{task_id}/ask", json={"question": question})
        return KnowledgeResult.model_validate(_check(res, f"ask {task_id}"))
