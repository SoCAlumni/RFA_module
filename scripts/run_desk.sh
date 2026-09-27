#!/usr/bin/env bash
# desk(대응 에이전트 상주 루프)를 띄운다. 먼저 scripts/run_services.sh 를 띄워 둘 것. Ctrl+C 로 종료.
#   RFA_CHANNELS 의 채널에서 새 멘션을 받아 결재함에 올리고, 거절된 안건을 다시 쓴다.
#   실제 LLM 으로 쓰려면 .env 의 RFA_LLM_MODE=openrouter(무료 nemotron) | nvidia | gemini | anthropic.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=${ENV_FILE:-.env}
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE — cp .env.example .env 후 값을 채우세요" >&2; exit 1; }
if ! curl -s -o /dev/null --max-time 1 http://127.0.0.1:8790/approvals; then
  echo "결재 서버(8790)가 안 떠 있어요. 먼저 scripts/run_services.sh 를 띄우세요." >&2
  exit 1
fi
exec uv run --env-file "$ENV_FILE" python -m rfa_workflow desk "$@"
