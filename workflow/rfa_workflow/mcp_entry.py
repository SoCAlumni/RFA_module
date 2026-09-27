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
    def run(mention: Mention, hint: str | None = None) -> RunResult:
        """멘션을 지식 조회 → 초안 → 첨삭 → 스캔 → 기밀검토까지 진행한다.

        outcome 이 returned 면 관련 업무·지식을 못 찾은 것이다. 질문을 보완하는 정보를 hint 로 주고
        같은 mention 으로 한 번 다시 부른다(같은 결재 문서를 이어 쓴다).
        """
        return run_graph(mention, deps, hint)

    return mcp


if __name__ == "__main__":
    build_server(Deps.from_env()).run()
