"""자동 복구 예산. 워크플로 실행 한 번에 쓸 수 있는 복구 횟수의 합계 상한.

재시도(일시적 오류), 409 해소(이미 반영됨), task 재선택이 모두 이 예산을 쓴다.
넘으면 RecoveryExhausted → needs_human. 쓴 내역(log)은 결과와 needs_human 사유에 남는다.
"""

from __future__ import annotations

MAX_RECOVERIES = 3


class RecoveryExhausted(Exception):
    """자동 복구 한도 초과."""


class RecoveryBudget:
    def __init__(self, limit: int = MAX_RECOVERIES) -> None:
        self.limit = limit
        self.log: list[str] = []

    def spend(self, reason: str) -> None:
        if len(self.log) >= self.limit:
            raise RecoveryExhausted(
                f"자동 복구 한도({self.limit}회) 초과: {reason} / 이전: {'; '.join(self.log)}"
            )
        self.log.append(reason)
