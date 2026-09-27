"""python -m rfa_workflow run --mention-json '{...}'   # 멘션 하나를 결재 대기까지
python -m rfa_workflow run --mention-file mention.json
python -m rfa_workflow redo <approval_id>             # 거절된 안건을 사유를 반영해 다시 쓴다
python -m rfa_workflow desk [--interval 5] [--once]    # 계속 돌며 멘션·거절 안건 처리

env: HEAD_URL, APPROVALS_URL, RFA_LLM_MODE(mock|openrouter|nvidia|gemini|anthropic), RFA_MODEL,
     provider 키(OPENROUTER_API_KEY | NVIDIA_API_KEY | GEMINI_API_KEY | ANTHROPIC_API_KEY),
     desk 는 추가로 RFA_CHANNELS 와 각 채널의 env (channels/registry.py)
run/redo 는 결과(RunResult)를 JSON 한 줄로 출력한다. failed 면 종료 코드 1.
desk 는 진행 상황을 로그(stderr)로 남긴다. Ctrl+C 로 끝낸다.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from channels.base import Channel
from channels.registry import make_channels
from rfa_common.contracts import ChannelKind, Mention

from rfa_workflow.clients import ServiceError
from rfa_workflow.deps import Deps
from rfa_workflow.desk import DEFAULT_INTERVAL, Desk
from rfa_workflow.graph import RunResult, redo, run


def main(
    argv: list[str] | None = None,
    deps: Deps | None = None,
    channels: Mapping[ChannelKind, Channel] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="rfa_workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run", help="멘션 하나를 결재 대기까지 처리")
    src = run_cmd.add_mutually_exclusive_group(required=True)
    src.add_argument("--mention-file", type=Path)
    src.add_argument("--mention-json")
    redo_cmd = sub.add_parser("redo", help="거절된 안건을 거절 사유를 반영해 다시 씀")
    redo_cmd.add_argument("approval_id", type=int)
    desk_cmd = sub.add_parser("desk", help="계속 돌며 채널 멘션과 거절된 안건을 처리")
    desk_cmd.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="틱 간격(초)")
    desk_cmd.add_argument("--once", action="store_true", help="한 틱만 돌고 끝냄")
    args = parser.parse_args(argv)
    deps = deps or Deps.from_env()

    if args.command == "desk":
        return run_desk(deps, channels, args.interval, args.once)
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


def run_desk(
    deps: Deps, channels: Mapping[ChannelKind, Channel] | None, interval: float, once: bool
) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )
    # 요청마다 찍히는 줄·소켓 세션 알림은 desk 로그를 가린다
    for noisy in ("httpx", "slack_sdk"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    channels = make_channels(os.environ) if channels is None else channels
    if not channels:
        logging.warning("RFA_CHANNELS 가 비어 있음 — 새 멘션은 받지 않고 거절된 안건만 처리")
    logging.info("desk 시작: 채널 %s, %s초마다", ", ".join(channels) or "없음", interval)
    for channel in channels.values():
        channel.start()  # Slack 소켓 연결 등. 설정 오류는 여기서 바로 드러난다
    try:
        Desk(channels, deps).run_forever(interval, ticks=1 if once else None)
    except KeyboardInterrupt:
        logging.info("desk 종료")
    return 0
