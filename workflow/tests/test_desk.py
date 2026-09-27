import json

import httpx
import pytest
from rfa_workflow.cli import main
from rfa_workflow.desk import McpError, McpTools, poll_once
from rfa_workflow.llm import RuleLLM
from wf_support import mention

URL = "http://127.0.0.1:8792/github/mcp"


def mcp_server(result=None, status=200, rpc_error=None, seen=None):
    """가짜 MCP 서버: tools/call 에 JSON-RPC 응답."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append((request.headers["authorization"], body["params"]["name"]))
        if rpc_error:
            return httpx.Response(status, json={"jsonrpc": "2.0", "id": 1, "error": rpc_error})
        return httpx.Response(status, json={"jsonrpc": "2.0", "id": 1, "result": result})

    return McpTools(httpx.Client(transport=httpx.MockTransport(handler)), URL, "mcp-secret")


def mentions_result(*items):
    return {"content": [], "structuredContent": {"result": list(items)}, "isError": False}


def test_poll_once_runs_workflow_for_each_new_mention(env):
    seen = []
    one = mention().model_dump(mode="json")
    two = {**one, "url": one["url"] + "0", "text": "@zetwhite 점심 뭐 먹어요?"}
    tools = mcp_server(mentions_result(one, two), seen=seen)

    results = poll_once(tools, env.make_deps(RuleLLM()))

    assert seen == [("Bearer mcp-secret", "list_mentions")]
    assert [(r.review_id, r.outcome) for r in results] == [(1, "reviewed"), (2, "returned")]


def test_poll_once_with_no_mentions_does_nothing(env):
    assert poll_once(mcp_server(mentions_result()), env.make_deps(RuleLLM())) == []


@pytest.mark.parametrize(
    ("tools", "match"),
    [
        (mcp_server(status=401), "HTTP 401"),
        (mcp_server(rpc_error={"code": -32601, "message": "no such tool"}), "no such tool"),
        (
            mcp_server(
                {"content": [{"type": "text", "text": "GithubError: 403"}], "isError": True}
            ),
            "GithubError: 403",
        ),
    ],
)
def test_mcp_failures_raise(tools, match):
    with pytest.raises(McpError, match=match):
        tools.call("list_mentions", {})


def test_cli_desk_once_prints_one_line_per_result(env, capsys):
    tools = mcp_server(mentions_result(mention().model_dump(mode="json")))
    assert main(["desk-once"], deps=env.make_deps(RuleLLM()), tools=tools) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert [json.loads(line)["outcome"] for line in lines] == ["reviewed"]
