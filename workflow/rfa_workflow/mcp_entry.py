"""stdio MCP 서버: OpenClaw public-desk 에이전트가 부르는 `run` 툴 하나 (Step 10 에서 등록).

실행: python -m rfa_workflow.mcp_entry
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from rfa_common.models import Mention

from rfa_workflow.deps import Deps
from rfa_workflow.graph_public import run as run_graph
from rfa_workflow.state import RunResult


def build_server(deps: Deps) -> MCPServer:
    mcp = MCPServer(
        name="rfa-workflow",
        instructions="GitHub 멘션 하나를 Public 대응 워크플로로 처리해 결재 대기까지 올린다.",
    )

    @mcp.tool()
    def run(mention: Mention) -> RunResult:
        """멘션을 받아 지식 조회 → 초안 → 첨삭 → 스캔 → 기밀검토까지 진행하고 결과를 돌려준다."""
        return run_graph(mention, deps)

    return mcp


if __name__ == "__main__":
    build_server(Deps.from_env()).run()
