"""결재 문서 저장소와 상태기계.

- 문서 하나 = 파일 하나: <state_dir>/review-<id>.json
- 쓰기는 tmp 파일에 쓴 뒤 rename (원자적). 한 프로세스 안의 동시 요청은 lock 으로 직렬화.
- 상태 전이는 ALLOWED 표에 있는 것만 허용하고, 모든 전이는 events 에 기록한다.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from rfa_common.models import Event, OpenReviewRequest, Review, ReviewStatus

S = ReviewStatus

# to 상태 → 허용되는 from 상태들. Step 4 에서 scanned, Step 5 에서 approved/rejected/posted 추가.
ALLOWED: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    S.KNOWLEDGE_READY: frozenset({S.OPENED}),
    S.DRAFTED: frozenset({S.KNOWLEDGE_READY}),
    S.REVIEWED: frozenset({S.DRAFTED}),
    S.NEEDS_HUMAN: frozenset({S.OPENED, S.KNOWLEDGE_READY, S.DRAFTED, S.SCANNED, S.REVIEWED}),
}


class ReviewNotFound(Exception):
    def __init__(self, review_id: int) -> None:
        super().__init__(f"review {review_id} not found")
        self.review_id = review_id


class InvalidTransition(Exception):
    def __init__(self, current: ReviewStatus, to: ReviewStatus) -> None:
        super().__init__(f"cannot move from {current} to {to}")
        self.current = current
        self.to = to


def _now() -> datetime:
    return datetime.now(UTC)


class ReviewStore:
    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def create(self, req: OpenReviewRequest, *, who: str) -> Review:
        with self._lock:
            review = Review(
                id=self._next_id(),
                status=S.OPENED,
                **req.model_dump(),
                events=[Event(at=_now(), who=who, what=S.OPENED)],
            )
            self._write(review)
            return review

    def get(self, review_id: int) -> Review:
        path = self._path(review_id)
        if not path.is_file():
            raise ReviewNotFound(review_id)
        return Review.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self, status: ReviewStatus | None = None) -> list[Review]:
        reviews = [self.get(i) for i in self._ids()]
        return [r for r in reviews if status is None or r.status == status]

    def advance(
        self,
        review_id: int,
        to: ReviewStatus,
        *,
        who: str,
        detail: str | None = None,
        mutate: Callable[[Review], None] | None = None,
    ) -> Review:
        """상태를 to 로 옮긴다. 허용되지 않은 전이면 InvalidTransition, 파일은 그대로."""
        with self._lock:
            review = self.get(review_id)
            if review.status not in ALLOWED.get(to, frozenset()):
                raise InvalidTransition(review.status, to)
            if mutate is not None:
                mutate(review)
            review.status = to
            review.events.append(Event(at=_now(), who=who, what=to, detail=detail))
            self._write(review)
            return review

    def _ids(self) -> list[int]:
        return sorted(int(p.stem.removeprefix("review-")) for p in self._dir.glob("review-*.json"))

    def _next_id(self) -> int:
        ids = self._ids()
        return (ids[-1] if ids else 0) + 1

    def _path(self, review_id: int) -> Path:
        return self._dir / f"review-{review_id}.json"

    def _write(self, review: Review) -> None:
        path = self._path(review.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(review.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)
