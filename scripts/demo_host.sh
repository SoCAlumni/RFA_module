#!/usr/bin/env bash
# 호스트 데모: GitHub 의 새 멘션을 가져와 워크플로로 결재 대기까지 올린다 (public-desk 대신).
# 먼저 scripts/run_services.sh 를 띄워 둘 것. 실제 Claude 를 쓰려면 RFA_LLM_MODE=anthropic.
#   RFA_LLM_MODE=anthropic scripts/demo_host.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --env-file "${ENV_FILE:-.env}" python -m rfa_workflow desk-once
