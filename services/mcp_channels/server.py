"""GitHub 채널 MCP 서버 (streamable HTTP, /github/mcp).

실행: uvicorn --factory mcp_channels.server:create_app --port 8792
필요한 env: GITHUB_TOKEN, RFA_GITHUB_LOGIN, RFA_GITHUB_REPOS, GITHUB_MCP_TOKEN
- GITHUB_MCP_TOKEN: 이 MCP 서버에 붙는 쪽(샌드박스)이 보내야 하는 bearer. GitHub 토큰과 다르다.
- RFA_MCP_ALLOWED_HOSTS: Host 헤더 허용 목록 (DNS rebinding 방지). 기본 127.0.0.1:*,localhost:*

에이전트에게는 읽기 툴만 노출한다. 게시(create_comment)는 여기 없다.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from rfa_common.models import Mention
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_channels.github import GithubClient, GithubConfig, MentionTracker
from mcp_channels.models import Thread

MCP_PATH = "/github/mcp"
DEFAULT_ALLOWED_HOSTS = "127.0.0.1:*,localhost:*"
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


class BearerAuth:
    """모든 HTTP 요청에 Authorization: Bearer <token> 을 요구하는 ASGI 래퍼."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(got, self._expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self._app(scope, receive, send)


def build_server(client: GithubClient, config: GithubConfig, tracker: MentionTracker) -> MCPServer:
    mcp = MCPServer(
        name="rfa-github",
        instructions="GitHub 채널 읽기 전용 툴. 답변 게시는 사람 결재 후 호스트가 한다.",
    )

    @mcp.tool(annotations=READ_ONLY)
    def list_mentions(since: datetime | None = None) -> list[Mention]:
        """감시 레포에서 나(@login)를 멘션한 새 이슈/댓글. 한 번 돌려준 건 다시 안 나온다."""
        return tracker.poll(client, config, since)

    @mcp.tool(annotations=READ_ONLY)
    def get_thread(target: str) -> Thread:
        """이슈/PR(owner/repo#N)의 제목, 본문, 최근 댓글 10개."""
        return client.get_thread(target)

    return mcp


def create_app(
    env: Mapping[str, str] | None = None, client: GithubClient | None = None
) -> BearerAuth:
    env = dict(os.environ if env is None else env)
    config = GithubConfig.from_env(env)
    mcp_token = env.get("GITHUB_MCP_TOKEN")
    if not mcp_token:
        raise RuntimeError("missing env: GITHUB_MCP_TOKEN (see .env.example)")
    state_dir = Path(env.get("RFA_DATA_DIR", "./data")) / "state"
    mcp = build_server(
        client or GithubClient(config.token),
        config,
        MentionTracker(state_dir / "mentions_seen.json"),
    )
    allowed = env.get("RFA_MCP_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS).split(",")
    app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=[h.strip() for h in allowed]),
    )
    return BearerAuth(app, mcp_token)
