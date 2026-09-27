"""desk: 계속 돌며 채널의 새 멘션을 그래프로 넘기고, 사람이 거절한 안건을 다시 쓰게 한다.

한 틱(tick)에 하는 일:
  1. 켜진 채널마다 poll() → 새 멘션마다 graph.run  → 결재함에 안건
  2. 전에 실패한 멘션 중 다시 할 때가 된 것 → graph.run
  3. 결재 서버의 rejected 안건 → graph.redo      → 사유를 반영한 새 초안

실패하면 RETRY_BASE·2^(n-1) 초 뒤에 다시 하고, MAX_ATTEMPTS 번째도 실패하면 포기한다
(매 틱 LLM 을 헛되이 부르지 않게). 포기한 rejected 안건은 desk 를 다시 켜면 다시 시도된다.
포기한 새 멘션은 잃는다 — 채널이 이미 '봤음' 으로 기록했기 때문 (데모 범위의 한계).

어떤 오류도 루프를 멈추지 않는다 (한 채널이 고장 나도 다른 채널과 거절 처리는 계속).
desk 는 채널의 poll 만 부른다. 게시(post)는 사람이 승인한 뒤 결재 서버가 한다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from channels.base import Channel
from rfa_common.contracts import ApprovalStatus, ChannelKind, Mention

from rfa_workflow.clients import ServiceError
from rfa_workflow.deps import Deps
from rfa_workflow.graph import RunResult, redo, run

log = logging.getLogger("rfa.desk")

DEFAULT_INTERVAL = 5.0
MAX_ATTEMPTS = 3
RETRY_BASE = 30.0  # 초. 첫 실패 30초 뒤, 두 번째 실패 60초 뒤 다시


@dataclass
class _Retry:
    attempts: int  # 지금까지 실패한 횟수
    next_at: float  # 이 시각(clock) 이후에 다시


class Desk:
    def __init__(
        self,
        channels: Mapping[ChannelKind, Channel],
        deps: Deps,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._channels = channels
        self._deps = deps
        self._clock = clock
        self._mentions: dict[str, tuple[Mention, _Retry]] = {}  # 멘션 주소 → 실패한 새 멘션
        self._redos: dict[int, _Retry] = {}  # 안건 id → 실패한 redo

    def tick(self) -> list[RunResult]:
        results = [self._run_mention(m) for m in self._poll_channels()]
        now = self._clock()
        for mention, retry in list(self._mentions.values()):
            if retry.next_at <= now:
                results.append(self._run_mention(mention))
        return results + self._redo_rejected()

    def run_forever(
        self,
        interval: float = DEFAULT_INTERVAL,
        sleep: Callable[[float], None] = time.sleep,
        ticks: int | None = None,
    ) -> None:
        """interval 초마다 tick. ticks 를 주면 그만큼만 돌고 끝난다 (테스트·--once 용)."""
        done = 0
        while ticks is None or done < ticks:
            self.tick()
            done += 1
            if ticks is None or done < ticks:
                sleep(interval)

    # ---- 1. 새 멘션 --------------------------------------------------------------

    def _poll_channels(self) -> list[Mention]:
        found: list[Mention] = []
        for kind, channel in self._channels.items():
            try:
                new = channel.poll()
            except Exception:
                log.exception("%s: 멘션을 가져오지 못함 — 다음 틱에 다시", kind)
                continue
            if new:
                log.info("%s: 새 멘션 %d개", kind, len(new))
            found += new
        return found

    def _run_mention(self, mention: Mention) -> RunResult:
        key = str(mention.url)
        result = self._safely(lambda: run(mention, self._deps), approval_id=None)
        where = f"{mention.channel} {mention.target}"
        if result.outcome == "pending":
            self._mentions.pop(key, None)
            log.info("%s → %s", where, result.summary)
            return result
        attempts = self._mentions[key][1].attempts + 1 if key in self._mentions else 1
        if attempts >= MAX_ATTEMPTS:
            self._mentions.pop(key, None)
            log.error("%s → 실패 %d번, 포기: %s", where, attempts, result.summary)
        else:
            self._mentions[key] = (mention, self._retry(attempts))
            log.warning("%s → 실패 %d번, 나중에 다시: %s", where, attempts, result.summary)
        return result

    # ---- 3. 거절된 안건 ------------------------------------------------------------

    def _redo_rejected(self) -> list[RunResult]:
        try:
            rejected = self._deps.approvals.list(ApprovalStatus.REJECTED)
        except ServiceError as exc:
            log.warning("거절 안건을 가져오지 못함 — 다음 틱에 다시: %s", exc)
            return []
        now = self._clock()
        results = []
        for approval in rejected:
            retry = self._redos.get(approval.id)
            if retry is not None and (retry.attempts >= MAX_ATTEMPTS or retry.next_at > now):
                continue
            result = self._safely(lambda a=approval: redo(a, self._deps), approval.id)
            results.append(result)
            if result.outcome == "pending":
                self._redos.pop(approval.id, None)
                log.info("안건 #%d 다시 씀 → %s", approval.id, result.summary)
                continue
            attempts = retry.attempts + 1 if retry else 1
            self._redos[approval.id] = self._retry(attempts)
            what = f"안건 #{approval.id} 다시 쓰기 실패 {attempts}번"
            if attempts >= MAX_ATTEMPTS:
                log.error("%s, 포기: %s", what, result.summary)
            else:
                log.warning("%s, 나중에 다시: %s", what, result.summary)
        return results

    # ---- 공통 --------------------------------------------------------------------

    def _retry(self, attempts: int) -> _Retry:
        return _Retry(attempts, self._clock() + RETRY_BASE * 2 ** (attempts - 1))

    @staticmethod
    def _safely(job: Callable[[], RunResult], approval_id: int | None) -> RunResult:
        """그래프가 예상하지 못한 오류(버그 등)도 루프를 멈추지 않게 실패 결과로 바꾼다."""
        try:
            return job()
        except Exception as exc:
            log.exception("예상하지 못한 오류")
            return RunResult(
                outcome="failed", approval_id=approval_id, summary=f"예상하지 못한 오류: {exc!r}"
            )
