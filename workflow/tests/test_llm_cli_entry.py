import asyncio
import json
from types import SimpleNamespace

import pytest
from rfa_workflow.cli import main
from rfa_workflow.llm import AnthropicLLM, LLMError, RuleLLM, make_llm
from rfa_workflow.mcp_entry import build_server
from rfa_workflow.nodes.press import EditOutput
from wf_support import mention


class FakeMessages:
    """anthropic.Anthropic().messages 흉내. 마지막 요청을 기록한다."""

    def __init__(self, response):
        self.response = response
        self.last = None

    def create(self, **kwargs):
        self.last = kwargs
        return self.response

    def parse(self, **kwargs):
        self.last = kwargs
        return self.response


def fake_client(stop_reason="end_turn", text="답변", parsed=None):
    response = SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        parsed_output=parsed,
    )
    return SimpleNamespace(messages=FakeMessages(response))


def call_text(llm):
    return llm.text(name="writer", system="S", user="U", context={})


def call_structured(llm):
    return llm.structured(name="editor", system="S", user="U", schema=EditOutput, context={})


def test_anthropic_text_joins_text_blocks_and_sends_model():
    client = fake_client(text="  초안  ")
    assert call_text(AnthropicLLM(client, model="m1")) == "초안"
    sent = client.messages.last
    assert (sent["model"], sent["system"]) == ("m1", "S")
    assert sent["messages"] == [{"role": "user", "content": "U"}]


def test_anthropic_structured_uses_parse_with_schema():
    parsed = EditOutput(verdict="pass", notes="", issues=[])
    client = fake_client(parsed=parsed)
    assert call_structured(AnthropicLLM(client)) is parsed
    assert client.messages.last["output_format"] is EditOutput


@pytest.mark.parametrize(
    ("client", "call", "match"),
    [
        (fake_client(stop_reason="refusal"), call_text, "refused"),
        (fake_client(stop_reason="max_tokens"), call_text, "truncated"),
        (fake_client(text="   "), call_text, "empty"),
        (fake_client(stop_reason="refusal"), call_structured, "refused"),
        (fake_client(parsed=None), call_structured, "no structured output"),
    ],
)
def test_anthropic_failures_become_llm_error(client, call, match):
    with pytest.raises(LLMError, match=match):
        call(AnthropicLLM(client))


def test_api_error_after_sdk_retries_becomes_llm_error():
    import anthropic
    import httpx2

    class Failing:
        def create(self, **kwargs):
            raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))

    with pytest.raises(LLMError, match="APIConnectionError"):
        call_text(AnthropicLLM(SimpleNamespace(messages=Failing())))


def test_api_status_error_message_is_kept_for_the_human():
    import anthropic
    import httpx2

    class Failing:
        def create(self, **kwargs):
            request = httpx2.Request("POST", "https://x")
            body = {"type": "error", "error": {"message": "Your credit balance is too low"}}
            raise anthropic.BadRequestError(
                "400", response=httpx2.Response(400, request=request), body=body
            )

    with pytest.raises(LLMError, match="writer: API 400 Your credit balance is too low"):
        call_text(AnthropicLLM(SimpleNamespace(messages=Failing())))


def test_make_llm_modes():
    assert isinstance(make_llm({}), RuleLLM)
    gateway = make_llm(
        {"RFA_LLM_MODE": "anthropic", "ANTHROPIC_BASE_URL": "https://inference.local"}
    )
    assert isinstance(gateway, AnthropicLLM)
    assert gateway._client.api_key == "unused"  # 샌드박스: 키는 게이트웨이가 넣는다
    assert gateway._model == "claude-opus-5"
    keyed = make_llm({"RFA_LLM_MODE": "anthropic", "ANTHROPIC_API_KEY": "k", "RFA_MODEL": "m2"})
    assert (keyed._client.api_key, keyed._model) == ("k", "m2")
    with pytest.raises(RuntimeError, match="unknown"):
        make_llm({"RFA_LLM_MODE": "gpt"})


def test_empty_base_url_in_env_falls_back_to_default(monkeypatch):
    """.env 의 `ANTHROPIC_BASE_URL=` (빈 값)이 SDK 에 빈 주소로 들어가지 않는다 (E2E 에서 발견)."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    env = {"RFA_LLM_MODE": "anthropic", "ANTHROPIC_API_KEY": "k", "ANTHROPIC_BASE_URL": ""}
    llm = make_llm(env)
    assert str(llm._client.base_url).rstrip("/") == "https://api.anthropic.com"
    assert llm._client.api_key == "k"


def test_rule_llm_keeps_decimals_and_ips_intact():
    from rfa_workflow.llm import _sentences

    text = "# 제목\n0.6B 모델은 10.12.3.4:8000 에서 돈다. 두 번째 문장! 세 번째?"
    assert _sentences(text) == [
        "0.6B 모델은 10.12.3.4:8000 에서 돈다.",
        "두 번째 문장!",
        "세 번째?",
    ]


def test_rule_llm_rejects_unknown_calls():
    with pytest.raises(LLMError):
        RuleLLM().text(name="censor_public", system="", user="", context={})
    with pytest.raises(LLMError):
        RuleLLM().structured(name="x", system="", user="", schema=EditOutput, context={})


def test_cli_runs_graph_and_prints_result(env, tmp_path, capsys):
    path = tmp_path / "m.json"
    path.write_text(mention().model_dump_json(), encoding="utf-8")

    code = main(["run", "--mention-file", str(path)], deps=env.make_deps(RuleLLM()))

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["outcome"] == "reviewed" and out["review_id"] == 1


def test_cli_requires_mention(capsys):
    with pytest.raises(SystemExit):
        main(["run"])


def test_mcp_entry_exposes_run_tool(env):
    server = build_server(env.make_deps(RuleLLM()))
    tools = asyncio.run(server.list_tools())
    assert [t.name for t in tools] == ["run"]

    result = asyncio.run(server.call_tool("run", {"mention": mention().model_dump(mode="json")}))

    assert not result.is_error
    assert result.structured_content["outcome"] == "reviewed"
