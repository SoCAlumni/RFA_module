"""python -m rfa_workflow [--env-file F] run --mention-file m.json  (또는 --mention-json '{...}')
python -m rfa_workflow [--env-file F] run --from-review 12 --hint "..."   # returned 문서 재요청
python -m rfa_workflow [--env-file F] desk-once   # GitHub MCP 의 새 멘션을 전부 처리

env: REVIEW_URL, KNOWLEDGE_URL, RFA_LLM_MODE(mock|anthropic), RFA_MODEL,
     ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY
결과(RunResult)를 JSON 한 줄로 출력한다.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rfa_common.models import Mention, Review

from rfa_workflow.deps import Deps
from rfa_workflow.desk import DEFAULT_MCP_URL, McpTools, poll_once
from rfa_workflow.graph_public import run


def load_env_file(path: Path) -> None:
    """KEY=VALUE 줄만 읽는다 (# 주석, 빈 줄 무시). 이미 있는 환경변수는 덮지 않는다.

    샌드박스에서는 설정을 파일 하나로 넘겨, exec 허용 목록에 고정 인자로 넣을 수 있게 한다.
    """
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def mention_from_review(review: Review) -> Mention:
    """저장된 결재 문서로 멘션을 되살린다 (returned 재요청 때 LLM 이 JSON 을 만들지 않게)."""
    return Mention(
        channel=review.channel,
        target=review.target,
        author=review.requester,
        text=review.question,
        url=review.source_url,
        created_at=review.events[0].at,
    )


def main(
    argv: list[str] | None = None, deps: Deps | None = None, tools: McpTools | None = None
) -> int:
    parser = argparse.ArgumentParser(prog="rfa-workflow")
    parser.add_argument("--env-file", type=Path, help="KEY=VALUE 설정 파일 (샌드박스용)")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run", help="멘션 하나를 결재 대기까지 처리")
    src = run_cmd.add_mutually_exclusive_group(required=True)
    src.add_argument("--mention-file", type=Path)
    src.add_argument("--mention-json")
    src.add_argument("--from-review", type=int, help="저장된 결재 문서의 멘션으로 다시 실행")
    run_cmd.add_argument("--hint", help="supervisor 재요청: 질문을 보완하는 정보 (returned 이후)")
    sub.add_parser(
        "desk-once",
        help="GitHub MCP 의 새 멘션을 모두 처리 (env: GITHUB_MCP_URL, GITHUB_MCP_TOKEN)",
    )
    args = parser.parse_args(argv)
    if args.env_file:
        load_env_file(args.env_file)
    deps = deps or Deps.from_env()

    if args.command == "desk-once":
        tools = tools or McpTools.connect(
            os.environ.get("GITHUB_MCP_URL", DEFAULT_MCP_URL), os.environ["GITHUB_MCP_TOKEN"]
        )
        results = poll_once(tools, deps)
    else:
        if args.from_review is not None:
            mention = mention_from_review(deps.review.get(args.from_review))
        else:
            raw = args.mention_file.read_text("utf-8") if args.mention_file else args.mention_json
            mention = Mention.model_validate_json(raw)
        results = [run(mention, deps, hint=args.hint)]
    for result in results:
        sys.stdout.write(result.model_dump_json() + "\n")
    return 0
