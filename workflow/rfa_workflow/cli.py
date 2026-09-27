"""python -m rfa_workflow run --mention-file mention.json  (또는 --mention-json '{...}')
python -m rfa_workflow desk-once   # GitHub MCP 의 새 멘션을 전부 처리 (호스트 데스크)

env: REVIEW_URL, KNOWLEDGE_URL, RFA_LLM_MODE(mock|anthropic), RFA_MODEL,
     ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY
결과(RunResult)를 JSON 한 줄로 출력한다.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rfa_common.models import Mention

from rfa_workflow.deps import Deps
from rfa_workflow.desk import DEFAULT_MCP_URL, McpTools, poll_once
from rfa_workflow.graph_public import run


def main(
    argv: list[str] | None = None, deps: Deps | None = None, tools: McpTools | None = None
) -> int:
    parser = argparse.ArgumentParser(prog="rfa-workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run", help="멘션 하나를 결재 대기까지 처리")
    src = run_cmd.add_mutually_exclusive_group(required=True)
    src.add_argument("--mention-file", type=Path)
    src.add_argument("--mention-json")
    run_cmd.add_argument("--hint", help="supervisor 재요청: 질문을 보완하는 정보 (returned 이후)")
    sub.add_parser(
        "desk-once",
        help="GitHub MCP 의 새 멘션을 모두 처리 (env: GITHUB_MCP_URL, GITHUB_MCP_TOKEN)",
    )
    args = parser.parse_args(argv)
    deps = deps or Deps.from_env()

    if args.command == "desk-once":
        tools = tools or McpTools.connect(
            os.environ.get("GITHUB_MCP_URL", DEFAULT_MCP_URL), os.environ["GITHUB_MCP_TOKEN"]
        )
        results = poll_once(tools, deps)
    else:
        raw = args.mention_file.read_text("utf-8") if args.mention_file else args.mention_json
        results = [run(Mention.model_validate_json(raw), deps, hint=args.hint)]
    for result in results:
        sys.stdout.write(result.model_dump_json() + "\n")
    return 0
