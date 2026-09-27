"""OpenAICompatLLM: OpenAI 호환 chat completions (NVIDIA Endpoints, Ollama, inference.local)."""

import json

import httpx
import pytest
from rfa_workflow.llm import LLMError, OpenAICompatLLM, make_llm
from rfa_workflow.nodes.censor import CensorOutput
from rfa_workflow.nodes.knowledge import _pick_schema
from rfa_workflow.nodes.press import EditOutput

EDIT_PASS = '{"verdict": "pass", "notes": "", "issues": []}'


def reply(content, finish_reason="stop", **message):
    body = {
        "choices": [{"message": {"content": content, **message}, "finish_reason": finish_reason}]
    }
    return httpx.Response(200, json=body)


class Server:
    """httpx.MockTransport 뒤에서 응답을 차례로 돌려주고 받은 요청을 기록한다."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.slept = []

    def handle(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def llm(self, **kwargs):
        http = httpx.Client(
            base_url="https://inference.local/v1", transport=httpx.MockTransport(self.handle)
        )
        return OpenAICompatLLM(http, sleep=self.slept.append, **kwargs)

    def sent(self, index=-1):
        return json.loads(self.requests[index].content)


def call_text(llm):
    return llm.text(name="writer", system="S", user="U", context={})


def call_structured(llm, schema=EditOutput):
    return llm.structured(name="editor", system="S", user="U", schema=schema, context={})


def test_text_posts_chat_completion_and_returns_content():
    server = Server(reply("  초안  "))
    assert call_text(server.llm(model="m1", max_tokens=99)) == "초안"
    assert str(server.requests[0].url) == "https://inference.local/v1/chat/completions"
    assert server.sent() == {
        "model": "m1",
        "max_tokens": 99,
        "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
    }


def test_text_ignores_reasoning_and_think_blocks():
    server = Server(reply("<think>따져 보면\n이렇다</think>\n답변", reasoning="생각 과정"))
    assert call_text(server.llm()) == "답변"


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (reply("잘린 글", finish_reason="length"), "truncated"),
        (reply("", finish_reason="content_filter"), "refused"),
        (reply("   "), "empty"),
        (reply(None, reasoning="생각만 하고 답이 없음"), "empty"),
        (httpx.Response(200, json={"choices": []}), "empty"),
    ],
)
def test_text_failures_become_llm_error(response, match):
    with pytest.raises(LLMError, match=match):
        call_text(Server(response).llm())


def test_structured_puts_schema_in_system_prompt_and_validates():
    server = Server(reply(EDIT_PASS))
    assert call_structured(server.llm()) == EditOutput(verdict="pass", notes="", issues=[])
    system = server.sent()["messages"][0]["content"]
    assert system.startswith("S\n\n[출력 형식]")
    assert json.loads(system[system.index("{") :]) == EditOutput.model_json_schema()


@pytest.mark.parametrize(
    "content",
    [
        f"```json\n{EDIT_PASS}\n```",
        f"판정은 다음과 같습니다.\n{EDIT_PASS}\n이상입니다.",
        f"<think>검토 중 {{}}</think>{EDIT_PASS}",
    ],
)
def test_structured_reads_json_wrapped_in_other_text(content):
    assert call_structured(Server(reply(content)).llm()).verdict == "pass"


def test_structured_accepts_the_workflow_schemas():
    censor = {
        "verdict": "redact",
        "redacted_body": "일정은 확정 후 공유",
        "reasons": [{"rule": "official:release-date", "span": "11/3", "action": "remove"}],
        "summary": "일정 제거",
    }
    out = call_structured(Server(reply(json.dumps(censor))).llm(), CensorOutput)
    assert out.reasons[0].action == "remove"
    pick = _pick_schema(["orbit", "prism"])
    out = call_structured(Server(reply('{"task_id": "orbit", "reason": "r"}')).llm(), pick)
    assert out.task_id == "orbit"


def test_structured_retries_once_with_the_error():
    server = Server(reply('{"verdict": "maybe", "notes": "", "issues": []}'), reply(EDIT_PASS))
    assert call_structured(server.llm()).verdict == "pass"
    assert len(server.requests) == 2
    retry = server.sent()["messages"][1]["content"]
    assert retry.startswith("U\n\n[이전 출력 오류")
    assert "'pass' or 'revise'" in retry


def test_structured_gives_up_after_second_bad_output():
    server = Server(reply("JSON 이 아님"), reply('{"verdict": "pass"}'))
    with pytest.raises(LLMError, match="editor: no structured output"):
        call_structured(server.llm())
    assert len(server.requests) == 2


def test_structured_stops_on_refusal_without_retry():
    server = Server(reply("", finish_reason="content_filter"), reply(EDIT_PASS))
    with pytest.raises(LLMError, match="refused"):
        call_structured(server.llm())
    assert len(server.requests) == 1


def test_transient_errors_are_retried_with_backoff():
    server = Server(
        httpx.Response(429, json={"error": {"message": "rate limit"}}),
        httpx.ConnectError("down"),
        reply("답변"),
    )
    assert call_text(server.llm()) == "답변"
    assert server.slept == [0.5, 1.0]


def test_transient_error_after_retries_becomes_llm_error():
    server = Server(*[httpx.Response(503, text="busy")] * 3)
    with pytest.raises(LLMError, match="writer: API 503 busy"):
        call_text(server.llm())
    assert len(server.requests) == 3
    server = Server(*[httpx.ConnectError("down")] * 3)
    with pytest.raises(LLMError, match="writer: API error ConnectError"):
        call_text(server.llm())


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"error": {"message": "model not found"}}, "model not found"),
        ({"error": "no compatible inference route available"}, "no compatible inference route"),
        ({"detail": "Not Found"}, "Not Found"),
    ],
)
def test_api_error_message_is_kept_for_the_human(body, detail):
    server = Server(httpx.Response(400, json=body), reply("답변"))
    with pytest.raises(LLMError, match=f"writer: API 400 {detail}"):
        call_text(server.llm())
    assert len(server.requests) == 1  # 재시도해도 같은 오류


def test_make_llm_openai_mode():
    gateway = make_llm({"RFA_LLM_MODE": "openai", "OPENAI_BASE_URL": "https://inference.local/v1"})
    assert isinstance(gateway, OpenAICompatLLM)
    assert str(gateway._http.base_url) == "https://inference.local/v1/"
    assert "authorization" not in gateway._http.headers  # 샌드박스: 키는 게이트웨이가 넣는다
    assert (gateway._model, gateway._max_tokens) == ("nvidia/nemotron-3-super-120b-a12b", 4096)

    keyed = make_llm(
        {
            "RFA_LLM_MODE": "openai",
            "OPENAI_BASE_URL": "",
            "OPENAI_API_KEY": "k",
            "RFA_MODEL": "m2",
            "RFA_MAX_TOKENS": "512",
        }
    )
    assert str(keyed._http.base_url) == "https://integrate.api.nvidia.com/v1/"
    assert keyed._http.headers["authorization"] == "Bearer k"
    assert (keyed._model, keyed._max_tokens) == ("m2", 512)

    nvidia = make_llm({"RFA_LLM_MODE": "openai", "NVIDIA_INFERENCE_API_KEY": "nv"})
    assert nvidia._http.headers["authorization"] == "Bearer nv"
