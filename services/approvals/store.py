"""결재 안건 저장소와 상태기계.

- 안건 하나 = 파일 하나: <state_dir>/approval-<id>.json  (DB 없음. 데모 규모)
- 쓰기는 tmp 파일에 쓴 뒤 rename (원자적). 한 프로세스 안의 동시 요청은 lock 으로 직렬화.
- 상태 전이는 ALLOWED 표에 있는 것만 허용하고, 모든 전이를 events 에 기록한다.

  pending → approved → posted          사람 승인 → 게시
  pending → rejected → pending          사람 거절 → desk 가 다시 써서 revise (round+1)
  rejected → closed                     MAX_ROUNDS 번째 거절이면 reject 가 바로 닫는다
"""

from __future__ import annotations

import os
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rfa_common.contracts import (
    Approval,
    ApprovalStatus,
    ApprovalSummary,
    ChannelKind,
    CreateApprovalRequest,
    Event,
)

S = ApprovalStatus
MAX_ROUNDS = 3
NO_TASK_KEY = "(none)"

# to 상태 → 허용되는 from 상태들.
ALLOWED: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    S.APPROVED: frozenset({S.PENDING}),
    S.POSTED: frozenset({S.APPROVED}),
    S.REJECTED: frozenset({S.PENDING}),
    S.PENDING: frozenset({S.REJECTED}),
    S.CLOSED: frozenset({S.REJECTED}),
}


class ApprovalNotFound(Exception):
    def __init__(self, approval_id: int) -> None:
        super().__init__(f"approval {approval_id} not found")
        self.approval_id = approval_id


class InvalidTransition(Exception):
    def __init__(self, current: ApprovalStatus, to: ApprovalStatus) -> None:
        super().__init__(f"cannot move from {current} to {to}")
        self.current = current
        self.to = to


@dataclass(frozen=True)
class Step:
    """상태 전이 한 번. mutate 는 전이 직전에 안건을 고친다."""

    to: ApprovalStatus
    who: str
    detail: str | None = None
    mutate: Callable[[Approval], None] | None = None


def now() -> datetime:
    return datetime.now(UTC)


class ApprovalStore:
    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def create(self, req: CreateApprovalRequest, *, who: str) -> tuple[Approval, bool]:
        """새 안건을 연다. 같은 source_url(같은 멘션)의 안건이 있으면 그걸 돌려준다.

        bool 은 새로 만들었는지. desk 가 같은 멘션을 두 번 올려도 안건이 하나로 유지된다.
        """
        with self._lock:
            for existing in self.list():
                if existing.source_url == req.source_url:
                    return existing, False
            at = now()
            approval = Approval(
                id=self._next_id(),
                status=S.PENDING,
                **req.model_dump(),
                events=[Event(at=at, who=who, what=S.PENDING)],
                created_at=at,
                updated_at=at,
            )
            self._write(approval)
            return approval, True

    def get(self, approval_id: int) -> Approval:
        path = self._path(approval_id)
        if not path.is_file():
            raise ApprovalNotFound(approval_id)
        return Approval.model_validate_json(path.read_text(encoding="utf-8"))

    def list(
        self,
        status: ApprovalStatus | None = None,
        channel: ChannelKind | None = None,
        task: str | None = None,
    ) -> list[Approval]:
        """조건에 맞는 안건을 updated_at 최신순으로. task 는 TaskRef.id."""
        found = [
            a
            for a in (self.get(i) for i in self._ids())
            if (status is None or a.status == status)
            and (channel is None or a.channel == channel)
            and (task is None or (a.task is not None and a.task.id == task))
        ]
        return sorted(found, key=lambda a: (a.updated_at, a.id), reverse=True)

    def summary(self) -> ApprovalSummary:
        by_channel: dict[str, Counter[str]] = defaultdict(Counter)
        by_task: dict[str, Counter[str]] = defaultdict(Counter)
        for a in self.list():
            by_channel[a.channel][a.status] += 1
            by_task[a.task.id if a.task else NO_TASK_KEY][a.status] += 1
        return ApprovalSummary(
            by_channel={k: dict(v) for k, v in by_channel.items()},
            by_task={k: dict(v) for k, v in by_task.items()},
        )

    def advance(self, approval_id: int, *steps: Step) -> Approval:
        """steps 를 차례로 적용하고 한 번에 저장한다.

        하나라도 허용되지 않은 전이면 InvalidTransition 을 던지고 파일은 그대로 둔다.
        """
        with self._lock:
            approval = self.get(approval_id)
            for step in steps:
                if approval.status not in ALLOWED[step.to]:
                    raise InvalidTransition(approval.status, step.to)
                if step.mutate is not None:
                    step.mutate(approval)
                at = now()
                approval.status = step.to
                approval.updated_at = at
                approval.events.append(Event(at=at, who=step.who, what=step.to, detail=step.detail))
            self._write(approval)
            return approval

    def _ids(self) -> list[int]:
        return sorted(
            int(p.stem.removeprefix("approval-")) for p in self._dir.glob("approval-*.json")
        )

    def _next_id(self) -> int:
        ids = self._ids()
        return (ids[-1] if ids else 0) + 1

    def _path(self, approval_id: int) -> Path:
        return self._dir / f"approval-{approval_id}.json"

    def _write(self, approval: Approval) -> None:
        path = self._path(approval.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(approval.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)
