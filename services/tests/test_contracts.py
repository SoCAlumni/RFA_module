"""계약 모델(rfa_common.contracts)과 contracts/*.openapi.yaml 이 서로 맞는지."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError
from rfa_common import contracts as c

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"


def mention(channel: c.ChannelKind = c.ChannelKind.GITHUB, target: str = "team/rfa-test#34"):
    return c.Mention(
        channel=channel,
        target=target,
        author="outside-dev",
        text="ORBIT 벤치마크 결과가 언제쯤 공개되나요?",
        url="https://github.com/team/rfa-test/issues/34#issuecomment-1",
        created_at=NOW,
    )


# ---- 채널별 target 형식 ----------------------------------------------------------


def test_github_target_ok_and_audience_public():
    m = mention()
    assert m.target == "team/rfa-test#34"
    assert m.audience == c.Audience.PUBLIC
    assert m.context == []


def test_slack_target_ok_and_audience_company():
    m = mention(c.ChannelKind.SLACK, "C0123ABC/1727000000.000100")
    assert m.audience == c.Audience.COMPANY


@pytest.mark.parametrize(
    "channel, target",
    [
        (c.ChannelKind.GITHUB, "rfa-test#34"),
        (c.ChannelKind.GITHUB, "team/rfa-test#0"),
        (c.ChannelKind.GITHUB, "C0123ABC/1727000000.000100"),  # slack 형식을 github 에
        (c.ChannelKind.SLACK, "team/rfa-test#34"),  # github 형식을 slack 에
        (c.ChannelKind.SLACK, "c0123/1727000000.000100"),  # 소문자 채널 id
        (c.ChannelKind.SLACK, "C0123ABC/1727000000"),  # ts 에 소수점 없음
    ],
)
def test_target_must_match_channel(channel, target):
    with pytest.raises(ValidationError):
        mention(channel, target)


def test_create_approval_checks_target_too():
    with pytest.raises(ValidationError):
        c.CreateApprovalRequest(
            channel=c.ChannelKind.SLACK,
            audience=c.Audience.COMPANY,
            target="team/rfa-test#34",
            source_url="https://slack.com/archives/C0123ABC/p1727000000000100",
            requester="product-team",
            question="PRISM 설계 문서 어디 있나요?",
            knowledge="...",
            draft="...",
        )


# ---- /ask -------------------------------------------------------------------


def test_ask_request_defaults_and_roundtrip():
    req = c.AskRequest(
        question="ORBIT 벤치마크 결과가 언제쯤 공개되나요?",
        channel=c.ChannelKind.GITHUB,
        audience=c.Audience.PUBLIC,
        target="team/rfa-test#34",
        url="https://github.com/team/rfa-test/issues/34#issuecomment-1",
        requester="outside-dev",
    )
    assert req.context == [] and req.feedback == []
    req.feedback.append(c.Rejection(draft="11/3 릴리즈…", reason="릴리즈 날짜", at=NOW))
    assert c.AskRequest.model_validate_json(req.model_dump_json()) == req


def test_ask_response_ok():
    task = c.TaskRef(id="orbit", name="ORBIT")
    res = c.AskResponse(knowledge="소폭 하락, QAT 보완 중", task=task)
    assert res.refusal is None


def test_ask_response_refusal_allows_empty_knowledge():
    res = c.AskResponse(knowledge="", refusal="관련 업무를 찾지 못했습니다")
    assert res.task is None


@pytest.mark.parametrize("knowledge", ["", "   "])
def test_ask_response_empty_knowledge_needs_refusal(knowledge):
    with pytest.raises(ValidationError):
        c.AskResponse(knowledge=knowledge)


# ---- approvals ---------------------------------------------------------------


def test_reject_request_reason_required():
    assert c.RejectRequest(reason="릴리즈 날짜").reason
    with pytest.raises(ValidationError):
        c.RejectRequest(reason="")


def test_approval_roundtrip_and_defaults():
    approval = c.Approval(
        id=12,
        status=c.ApprovalStatus.PENDING,
        channel=c.ChannelKind.GITHUB,
        audience=c.Audience.PUBLIC,
        target="team/rfa-test#34",
        source_url="https://github.com/team/rfa-test/issues/34#issuecomment-1",
        requester="outside-dev",
        question="ORBIT 벤치마크 결과가 언제쯤 공개되나요?",
        knowledge="소폭 하락, QAT 보완 중",
        draft="안녕하세요. …",
        created_at=NOW,
        updated_at=NOW,
    )
    assert approval.round == 1 and approval.task is None and approval.posted_url is None
    assert approval.rejections == [] and approval.events == []
    approval.rejections.append(c.Rejection(draft="…", reason="릴리즈 날짜", at=NOW))
    approval.events.append(c.Event(at=NOW, who="human", what="rejected", detail="릴리즈 날짜"))
    assert c.Approval.model_validate_json(approval.model_dump_json()) == approval


def test_approval_round_positive():
    assert c.Approval.model_validate(_approval_dict()).round == 1
    with pytest.raises(ValidationError):
        c.Approval.model_validate({**_approval_dict(), "round": 0})


def test_revise_request_defaults():
    req = c.ReviseApprovalRequest(knowledge="k", draft="d")
    assert req.task is None and req.refusal is None


def _approval_dict() -> dict:
    return {
        "id": 1,
        "status": "pending",
        "channel": "slack",
        "audience": "company",
        "target": "C0123ABC/1727000000.000100",
        "source_url": "https://slack.com/archives/C0123ABC/p1727000000000100",
        "requester": "product-team",
        "question": "q",
        "knowledge": "k",
        "draft": "d",
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }


def test_summary_shape():
    s = c.ApprovalSummary(by_channel={"github": {"pending": 2}}, by_task={"(none)": {"posted": 1}})
    assert s.by_channel["github"]["pending"] == 2


# ---- yaml ↔ 모델 대조 -----------------------------------------------------------

SCHEMA_MODELS: dict[str, list[type[BaseModel]]] = {
    "head": [c.ThreadMessage, c.Rejection, c.TaskRef, c.AskRequest, c.AskResponse],
    "approvals": [
        c.ThreadMessage,
        c.Rejection,
        c.TaskRef,
        c.Event,
        c.CreateApprovalRequest,
        c.ReviseApprovalRequest,
        c.RejectRequest,
        c.Approval,
        c.ApprovalSummary,
    ],
}
ENUM_MODELS = {
    "ChannelKind": c.ChannelKind,
    "Audience": c.Audience,
    "ApprovalStatus": c.ApprovalStatus,
}


def _schemas(name: str) -> dict:
    spec = yaml.safe_load((CONTRACTS_DIR / f"{name}.openapi.yaml").read_text(encoding="utf-8"))
    return spec["components"]["schemas"]


def _flatten(schema: dict, all_schemas: dict) -> tuple[set[str], set[str]]:
    """allOf 를 합쳐 (properties, required) 를 돌려준다."""
    props: set[str] = set()
    required: set[str] = set()
    for part in schema.get("allOf", [schema]):
        if "$ref" in part:
            part = all_schemas[part["$ref"].rsplit("/", 1)[1]]
            p, r = _flatten(part, all_schemas)
            props |= p
            required |= r
        props |= set(part.get("properties", {}))
        required |= set(part.get("required", []))
    return props, required


@pytest.mark.parametrize(
    "spec_name, model",
    [(n, m) for n, models in SCHEMA_MODELS.items() for m in models],
    ids=lambda x: x if isinstance(x, str) else x.__name__,
)
def test_yaml_properties_match_model_fields(spec_name, model):
    schemas = _schemas(spec_name)
    props, required = _flatten(schemas[model.__name__], schemas)
    fields = model.model_fields
    assert props == set(fields), f"{spec_name}.yaml {model.__name__}: properties ≠ fields"
    model_required = {n for n, f in fields.items() if f.is_required()}
    assert model_required <= required, f"{model.__name__}: 모델 필수 필드가 yaml required 에 없음"


@pytest.mark.parametrize("spec_name", ["head", "approvals"])
def test_yaml_enums_match(spec_name):
    schemas = _schemas(spec_name)
    for name, enum in ENUM_MODELS.items():
        if name in schemas:
            assert set(schemas[name]["enum"]) == {e.value for e in enum}, name
