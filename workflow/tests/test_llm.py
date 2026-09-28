import json
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest
from rfa_common.contracts import AskResponse
from rfa_workflow.llm import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    OPENAI_COMPAT_PROVIDERS,
    AnthropicLLM,
    LLMError,
    OpenAICompatLLM,
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
    return AnthropicLLM(client, DEFAULT_MODEL), messages


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
        "model": "claude-sonnet-4-6",
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


def openai_compat_with(handler) -> tuple[OpenAICompatLLM, list[httpx.Request]]:
    """MockTransport 를 붙인 OpenAICompatLLM. handler 는 응답(dict|Response)이거나 호출 가능."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(handler, httpx.Response):
            return handler
        return httpx.Response(200, json=handler)

    model = "nvidia/nemotron-3.5-lightning:free"
    llm = OpenAICompatLLM("https://openrouter.ai/api/v1", "KEY", model)
    llm._client = httpx.Client(
        base_url="https://openrouter.ai/api/v1",
        headers={"Authorization": "Bearer KEY"},
        transport=httpx.MockTransport(respond),
    )
    return llm, seen


def completion(content, finish_reason: str = "stop") -> dict:
    return {"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]}


def test_openai_compat_request_shape():
    llm, seen = openai_compat_with(completion(" 안녕하세요. 답입니다. "))
    out = llm.text(name="writer", system="SYS", user="USER", context={})
    assert out == "안녕하세요. 답입니다."
    [request] = seen
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer KEY"
    assert json.loads(request.content) == {
        "model": "nvidia/nemotron-3.5-lightning:free",
        "max_tokens": 16000,
        "messages": [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USER"},
        ],
    }


@pytest.mark.parametrize(
    "response, message",
    [
        (completion("잘린", "length"), "writer: output truncated"),
        (completion("", "content_filter"), "writer: model refused"),
        (completion("  "), "writer: empty response"),
        (completion(None), "writer: empty response"),  # 일부 서버는 content 를 null 로 준다
        ({"choices": []}, "writer: empty response"),
    ],
)
def test_openai_compat_bad_responses(response, message):
    llm, _ = openai_compat_with(response)
    with pytest.raises(LLMError, match=message):
        llm.text(name="writer", system="S", user="U", context={})


def test_openai_compat_status_error_shows_cause():
    body = {"error": {"message": "No auth credentials found"}}
    llm, _ = openai_compat_with(httpx.Response(401, json=body))
    with pytest.raises(LLMError, match="writer: API 401 No auth credentials found"):
        llm.text(name="writer", system="S", user="U", context={})


def test_openai_compat_list_error_body_shows_cause():
    # Gemini 의 OpenAI 호환 엔드포인트는 오류를 [{"error": {...}}] 리스트로 준다
    body = [
        {"error": {"code": 404, "message": "models/gemini-x is not found", "status": "NOT_FOUND"}}
    ]
    llm, _ = openai_compat_with(httpx.Response(404, json=body))
    with pytest.raises(LLMError, match="writer: API 404 models/gemini-x is not found"):
        llm.text(name="writer", system="S", user="U", context={})


def test_openai_compat_connection_error():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    llm = OpenAICompatLLM("https://openrouter.ai/api/v1", "KEY", "m")
    llm._client = httpx.Client(
        base_url="https://openrouter.ai/api/v1", transport=httpx.MockTransport(boom)
    )
    with pytest.raises(LLMError, match="writer: API error ConnectError"):
        llm.text(name="writer", system="S", user="U", context={})


def test_rule_llm_copies_knowledge_or_declines():
    rule = RuleLLM()
    ok = rule.text(name="writer", system="", user="", context={"ask": AskResponse(knowledge="K.")})
    assert ok == "안녕하세요. K."
    refused = AskResponse(knowledge="", refusal="없음")
    assert "어려워요" in rule.text(name="writer", system="", user="", context={"ask": refused})
    with pytest.raises(LLMError):
        rule.text(name="editor", system="", user="", context={})


def test_make_llm():
    assert isinstance(make_llm({"RFA_LLM_MODE": "mock"}), RuleLLM)
    with pytest.raises(RuntimeError, match="RFA_LLM_MODE is not set"):
        make_llm({})
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


def test_openai_compat_extra_fields_in_request():
    """nemotron 은 reasoning 을 꺼야 한다 — provider 별 추가 필드가 요청 본문에 실리는지."""
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=completion("답"))

    llm = OpenAICompatLLM(
        "https://x", "K", "m", extra={"chat_template_kwargs": {"enable_thinking": False}}
    )
    llm._client = httpx.Client(base_url="https://x", transport=httpx.MockTransport(respond))
    llm.text(name="writer", system="S", user="U", context={})
    body = json.loads(seen[0].content)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("mode", ["openrouter", "nvidia", "gemini"])
def test_make_llm_openai_compat_providers(mode):
    base_url, key_var, default_model, extra = OPENAI_COMPAT_PROVIDERS[mode]
    llm = make_llm({"RFA_LLM_MODE": mode, key_var: "k"})
    assert isinstance(llm, OpenAICompatLLM)
    assert llm._model == default_model
    assert llm._base_url == base_url
    assert llm._client.headers["Authorization"] == "Bearer k"
    assert llm._extra == extra
    overridden = make_llm({"RFA_LLM_MODE": mode, key_var: "k", "RFA_MODEL": "m"})
    assert overridden._model == "m"


def test_default_free_model_is_nemotron():
    llm = make_llm({"RFA_LLM_MODE": "openrouter"})
    assert llm._model == "nvidia/nemotron-3.5-lightning:free"


def test_prompts_exist():
    assert "[거절 이력]" in prompt("writer")
    assert "GitHub" in prompt("style_github") and "Slack" in prompt("style_slack")
