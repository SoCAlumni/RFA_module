import logging

import pytest
from channels.base import ChannelError
from rfa_workflow.desk import MAX_ATTEMPTS, RETRY_BASE, Desk
from rfa_workflow.llm import LLMError, RuleLLM
from wf_support import FakeLLM, github_mention, slack_mention

HEAD_DOWN = [503] * 4  # 한 번 실행에서 재시도 3번까지 전부 실패


class FakeChannel:
    """poll 할 때마다 batches 에서 하나씩 꺼내 돌려준다. 값이 Exception 이면 던진다."""

    def __init__(self, kind: str, *batches) -> None:
        self.kind = kind
        self.batches = list(batches)
        self.polls = 0

    def poll(self):
        self.polls += 1
        batch = self.batches.pop(0) if self.batches else []
        if isinstance(batch, Exception):
            raise batch
        return batch

    def post(self, target: str, body: str) -> str:
        raise AssertionError("desk 는 게시하지 않는다")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def outcomes(results) -> list[tuple[str, int | None]]:
    return [(r.outcome, r.approval_id) for r in results]


# ---- 새 멘션 -----------------------------------------------------------------------


def test_new_mentions_become_approvals(env, clock):
    github = FakeChannel("github", [github_mention()], [])
    slack = FakeChannel("slack", [slack_mention()])
    desk = Desk({"github": github, "slack": slack}, env.deps(RuleLLM()), clock)

    assert outcomes(desk.tick()) == [("pending", 1), ("pending", 2)]
    assert outcomes(desk.tick()) == []  # 새 멘션도, 거절된 안건도 없음
    assert (env.approval(1)["channel"], env.approval(2)["channel"]) == ("github", "slack")


def test_broken_channel_does_not_stop_others(env, clock, caplog):
    broken = FakeChannel("github", ChannelError("401 bad credentials"))
    slack = FakeChannel("slack", [slack_mention()])
    desk = Desk({"github": broken, "slack": slack}, env.deps(RuleLLM()), clock)

    with caplog.at_level(logging.ERROR, logger="rfa.desk"):
        assert outcomes(desk.tick()) == [("pending", 1)]
    assert "github: 멘션을 가져오지 못함" in caplog.text
    assert broken.polls == 1


def test_failed_mention_is_retried_later(env, clock):
    env.head.faults[("post", "/ask")] = list(HEAD_DOWN)
    desk = Desk({"github": FakeChannel("github", [github_mention()])}, env.deps(RuleLLM()), clock)

    assert outcomes(desk.tick()) == [("failed", None)]
    assert outcomes(desk.tick()) == []  # 아직 때가 안 됨 → head·LLM 을 부르지 않음
    clock.now += RETRY_BASE
    assert outcomes(desk.tick()) == [("pending", 1)]  # head 가 살아나 성공
    clock.now += 10 * RETRY_BASE
    assert outcomes(desk.tick()) == []  # 성공한 멘션은 다시 하지 않음


def test_mention_is_given_up_after_max_attempts(env, clock, caplog):
    env.head.faults[("post", "/ask")] = HEAD_DOWN * MAX_ATTEMPTS
    desk = Desk({"github": FakeChannel("github", [github_mention()])}, env.deps(RuleLLM()), clock)

    with caplog.at_level(logging.ERROR, logger="rfa.desk"):
        for attempt in range(MAX_ATTEMPTS):
            assert outcomes(desk.tick()) == [("failed", None)]
            clock.now += RETRY_BASE * 2**attempt  # 30, 60, 120 초
    assert "실패 3번, 포기" in caplog.text
    clock.now += 1000
    assert outcomes(desk.tick()) == []
    assert env.approvals.seen == [("get", "/approvals", None)] * (MAX_ATTEMPTS + 1)  # 목록 조회만


def test_unexpected_error_is_contained(env, clock):
    llm = FakeLLM([RuntimeError("bug"), "두 번째는 성공"])
    desk = Desk({"github": FakeChannel("github", [github_mention()])}, env.deps(llm), clock)

    (result,) = desk.tick()
    assert (
        result.outcome == "failed" and result.summary == "예상하지 못한 오류: RuntimeError('bug')"
    )
    clock.now += RETRY_BASE
    assert outcomes(desk.tick()) == [("pending", 1)]


# ---- 거절된 안건 ---------------------------------------------------------------------


def test_rejected_approval_is_redone_once(env, clock):
    desk = Desk({"github": FakeChannel("github", [github_mention()])}, env.deps(RuleLLM()), clock)
    desk.tick()
    env.reject(1, "릴리즈 날짜가 들어가 있음")

    (result,) = desk.tick()
    assert (result.outcome, result.approval_id, result.round) == ("pending", 1, 2)
    assert "11/3" not in env.approval(1)["draft"]
    assert desk.tick() == []  # 이제 pending → 다시 하지 않음


def test_failed_redo_backs_off_then_gives_up(env, clock, caplog):
    llm = FakeLLM(["첫 초안", *[LLMError("writer: model refused")] * MAX_ATTEMPTS])
    desk = Desk({"github": FakeChannel("github", [github_mention()])}, env.deps(llm), clock)
    desk.tick()
    env.reject(1, "다시")

    with caplog.at_level(logging.ERROR, logger="rfa.desk"):
        for attempt in range(MAX_ATTEMPTS):
            assert outcomes(desk.tick()) == [("failed", 1)]
            assert desk.tick() == []  # 간격 안에서는 다시 안 함
            clock.now += RETRY_BASE * 2**attempt
    assert "안건 #1 다시 쓰기 실패 3번, 포기" in caplog.text
    assert desk.tick() == []
    assert len(llm.calls) == 1 + MAX_ATTEMPTS  # 첫 초안 + 실패 3번, 그 뒤로는 LLM 을 안 부름
    assert env.approval(1)["status"] == "rejected"


def test_approvals_down_still_handles_mentions(env, clock, caplog):
    env.approvals.faults[("get", "/approvals")] = [503] * 4
    desk = Desk({"github": FakeChannel("github", [github_mention()])}, env.deps(RuleLLM()), clock)

    with caplog.at_level(logging.WARNING, logger="rfa.desk"):
        assert outcomes(desk.tick()) == [("pending", 1)]
    assert "거절 안건을 가져오지 못함" in caplog.text


# ---- 루프 ----------------------------------------------------------------------------


def test_run_forever_sleeps_between_ticks(env, clock):
    channel = FakeChannel("github")
    sleeps: list[float] = []
    Desk({"github": channel}, env.deps(RuleLLM()), clock).run_forever(2.5, sleeps.append, ticks=3)
    assert channel.polls == 3
    assert sleeps == [2.5, 2.5]  # 마지막 틱 뒤에는 자지 않음
