from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from rfa_common.contracts import AskResponse
from rfa_workflow.llm import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    AnthropicLLM,
    LLMError,
    RuleLLM,
    make_llm,
    prompt,
)

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def block(type_: str, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(type=type_, text=text, thinking="")


class FakeMessages:
    """client.beta.messages 자리. 받은 인자를 기록하고 정해 둔 응답(또는 예외)을 돌려준다."""

    def __init__(self, result):
        self.result = result
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def llm_with(result) -> tuple[AnthropicLLM, FakeMessages]:
    messages = FakeMessages(result)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return AnthropicLLM(client, "claude-opus-5"), messages


def reply(stop_reason: str, *blocks) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


def call(llm: AnthropicLLM) -> str:
    return llm.text(name="writer", system="SYS", user="USER", context={})


def test_anthropic_request_shape_and_text_only():
    llm, messages = llm_with(
        reply(
            "end_turn", block("thinking"), block("text", "안녕하세요. "), block("text", "답입니다.")
        )
    )
    assert call(llm) == "안녕하세요. 답입니다."
    assert messages.kwargs == {
        "model": "claude-opus-5",
        "max_tokens": 16000,
        "system": "SYS",
        "messages": [{"role": "user", "content": "USER"}],
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


@pytest.mark.parametrize(
    "response, message",
    [
        (reply("refusal"), "writer: model refused"),
        (reply("max_tokens", block("text", "잘린")), "writer: output truncated"),
        (reply("end_turn", block("thinking"), block("text", "  ")), "writer: empty response"),
    ],
)
def test_anthropic_bad_stop_reasons(response, message):
    llm, _ = llm_with(response)
    with pytest.raises(LLMError, match=message):
        call(llm)


def test_anthropic_status_error_shows_cause():
    err = anthropic.RateLimitError(
        "rate",
        response=httpx2.Response(429, request=REQ),
        body={"error": {"message": "slow down"}},
    )
    llm, _ = llm_with(err)
    with pytest.raises(LLMError, match="writer: API 429 slow down"):
        call(llm)


def test_anthropic_connection_error():
    llm, _ = llm_with(anthropic.APIConnectionError(request=REQ))
    with pytest.raises(LLMError, match="writer: API error APIConnectionError"):
        call(llm)


def test_rule_llm_copies_knowledge_or_declines():
    rule = RuleLLM()
    ok = rule.text(name="writer", system="", user="", context={"ask": AskResponse(knowledge="K.")})
    assert ok == "안녕하세요. K."
    refused = AskResponse(knowledge="", refusal="없음")
    assert "어려워요" in rule.text(name="writer", system="", user="", context={"ask": refused})
    with pytest.raises(LLMError):
        rule.text(name="editor", system="", user="", context={})


def test_make_llm():
    assert isinstance(make_llm({}), RuleLLM)
    real = make_llm(
        {"RFA_LLM_MODE": "anthropic", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": ""}
    )
    assert isinstance(real, AnthropicLLM)
    assert real._model == DEFAULT_MODEL
    assert str(real._client.base_url).startswith("https://api.anthropic.com")
    other = make_llm({"RFA_LLM_MODE": "anthropic", "ANTHROPIC_API_KEY": "k", "RFA_MODEL": "m"})
    assert other._model == "m"
    with pytest.raises(RuntimeError, match="unknown RFA_LLM_MODE"):
        make_llm({"RFA_LLM_MODE": "gpt"})


def test_prompts_exist():
    assert "[거절 이력]" in prompt("writer")
    assert "GitHub" in prompt("style_github") and "Slack" in prompt("style_slack")
