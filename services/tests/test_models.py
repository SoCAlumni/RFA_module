from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError
from rfa_common.models import (
    Channel,
    Decision,
    EditDecision,
    EditVerdict,
    Event,
    KnowledgeResult,
    Mention,
    ReasonAction,
    Review,
    ReviewStatus,
    ScanHit,
    ScanType,
    TaskInfo,
    Verdict,
    VerdictDecision,
    VerdictReason,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def test_knowledge_result_roundtrip():
    result = KnowledgeResult(
        task_id="triv3",
        answer="...",
        confidence=0.6,
        sources=["TRIV3 9월 진행 현황: INT4 이후 소폭 하락"],
    )
    assert KnowledgeResult.model_validate(result.model_dump()) == result


def test_knowledge_result_sources_default_empty():
    assert KnowledgeResult(task_id="triv3", answer="...", confidence=0.6).sources == []


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_knowledge_result_rejects_confidence_out_of_range(confidence):
    with pytest.raises(ValidationError):
        KnowledgeResult(task_id="triv3", answer="...", confidence=confidence)


def test_task_info():
    info = TaskInfo(id="triv3", name="TRIV3", description="벤치마크", updated_at=date(2026, 9, 24))
    assert info.id == "triv3"


def make_mention(target: str = "zetwhite/rfa-test#34") -> Mention:
    return Mention(
        channel=Channel.PUBLIC,
        target=target,
        author="someone",
        text="TRIV3 벤치마크 진행 어때?",
        url="https://github.com/zetwhite/rfa-test/issues/34",
        created_at=NOW,
    )


def test_mention_accepts_owner_repo_number():
    assert make_mention().target == "zetwhite/rfa-test#34"


@pytest.mark.parametrize(
    "target", ["rfa-test#34", "zetwhite/rfa-test", "zetwhite/rfa-test#0", "a/b#x"]
)
def test_mention_rejects_bad_target(target):
    with pytest.raises(ValidationError):
        make_mention(target)


def test_edit_verdict_round_must_be_positive():
    assert EditVerdict(round=1, verdict=EditDecision.PASS).issues == []
    with pytest.raises(ValidationError):
        EditVerdict(round=0, verdict=EditDecision.PASS)


def test_scan_hit():
    hit = ScanHit(type=ScanType.PRIVATE_IP, match="10.12.3.4", span=(88, 97))
    assert hit.span == (88, 97)


def test_verdict_reason_rule_format():
    reason = VerdictReason(
        rule="official:release-date", span="11/3 릴리즈", action=ReasonAction.REMOVE
    )
    assert reason.rule.startswith("official:")
    with pytest.raises(ValidationError):
        VerdictReason(rule="release-date", span="x", action=ReasonAction.REMOVE)
    with pytest.raises(ValidationError):
        VerdictReason(rule="legal:Release Date", span="x", action=ReasonAction.REMOVE)


def test_verdict_redact_requires_redacted_body():
    with pytest.raises(ValidationError):
        Verdict(verdict=VerdictDecision.REDACT, summary="x")
    ok = Verdict(verdict=VerdictDecision.REDACT, redacted_body="정리본", summary="x")
    assert ok.redacted_body == "정리본"


def test_verdict_allow_without_body():
    assert Verdict(verdict=VerdictDecision.ALLOW, summary="문제 없음").redacted_body is None


def test_review_minimal_and_full():
    review = Review(
        id=12,
        status=ReviewStatus.OPENED,
        channel=Channel.PUBLIC,
        target="zetwhite/rfa-test#34",
        source_url="https://github.com/zetwhite/rfa-test/issues/34",
        requester="someone",
        question="TRIV3 벤치마크 진행 어때?",
        events=[Event(at=NOW, who="intake", what="opened")],
    )
    assert review.knowledge is None and review.edit_log == [] and review.scan == []
    review.decision = Decision(by="human", at=NOW)
    assert Review.model_validate_json(review.model_dump_json()) == review
