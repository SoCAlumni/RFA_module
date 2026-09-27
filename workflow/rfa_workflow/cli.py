"""python -m rfa_workflow run --mention-json '{...}'   # 멘션 하나를 결재 대기까지
python -m rfa_workflow run --mention-file mention.json
python -m rfa_workflow redo <approval_id>             # 거절된 안건을 사유를 반영해 다시 쓴다

env: HEAD_URL, APPROVALS_URL, RFA_LLM_MODE(mock|anthropic), RFA_MODEL, ANTHROPIC_API_KEY
결과(RunResult)를 JSON 한 줄로 출력한다. failed 면 종료 코드 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rfa_common.contracts import Mention

from rfa_workflow.clients import ServiceError
from rfa_workflow.deps import Deps
from rfa_workflow.graph import RunResult, redo, run


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rfa_workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run", help="멘션 하나를 결재 대기까지 처리")
    src = run_cmd.add_mutually_exclusive_group(required=True)
    src.add_argument("--mention-file", type=Path)
    src.add_argument("--mention-json")
    redo_cmd = sub.add_parser("redo", help="거절된 안건을 거절 사유를 반영해 다시 씀")
    redo_cmd.add_argument("approval_id", type=int)
    args = parser.parse_args(argv)
    deps = deps or Deps.from_env()

    if args.command == "run":
        raw = args.mention_file.read_text("utf-8") if args.mention_file else args.mention_json
        result = run(Mention.model_validate_json(raw), deps)
    else:
        try:
            result = redo(deps.approvals.get(args.approval_id), deps)
        except ServiceError as exc:
            result = RunResult(outcome="failed", approval_id=args.approval_id, summary=str(exc))
    sys.stdout.write(result.model_dump_json() + "\n")
    return 0 if result.outcome == "pending" else 1
