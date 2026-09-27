"""호스트용 데스크: GitHub MCP 에서 새 멘션을 받아 하나씩 워크플로에 넣는다.

Step 10 에서는 OpenClaw public-desk 에이전트가 이 역할을 한다. 이 모듈은 샌드박스 없이
전체 흐름을 돌리는 호스트 E2E(Step 9)와, 샌드박스 연결이 막혔을 때의 대안(호스트 러너)용이다.
returned(supervisor 보완 필요)는 여기서 다시 부르지 않고 보고만 한다 (LLM supervisor 가 없으므로).
"""

from __future__ import annotations

from typing import Any

import httpx
from rfa_common.models import Mention

from rfa_workflow.clients import TIMEOUT, HttpLike
from rfa_workflow.deps import Deps
from rfa_workflow.graph_public import run
from rfa_workflow.state import RunResult

DEFAULT_MCP_URL = "http://127.0.0.1:8792/github/desk/mcp"


class McpError(Exception):
    """MCP 툴 호출 실패."""


class McpTools:
    """최소 MCP 클라이언트. stateless JSON 모드 서버(우리 mcp_channels)에 tools/call 을 보낸다."""

    def __init__(self, http: HttpLike, url: str, token: str) -> None:
        self._http = http
        self._url = url
        self._headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
        }

    @classmethod
    def connect(cls, url: str, token: str) -> McpTools:
        return cls(httpx.Client(timeout=TIMEOUT), url, token)

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        res = self._http.post(self._url, json=body, headers=self._headers)
        if res.status_code != 200:
            raise McpError(f"{name}: HTTP {res.status_code}")
        payload = res.json()
        if "error" in payload:
            raise McpError(f"{name}: {payload['error'].get('message')}")
        result = payload["result"]
        if result.get("isError"):
            raise McpError(f"{name}: {result['content'][0]['text']}")
        content = result["structuredContent"]
        # 목록 같은 비객체 반환값만 {"result": ...} 로 감싸진다 (객체는 그대로)
        return content["result"] if set(content) == {"result"} else content


def poll_once(tools: McpTools, deps: Deps) -> list[RunResult]:
    mentions = [Mention.model_validate(m) for m in tools.call("list_mentions", {})]
    return [run(m, deps) for m in mentions]
