#!/usr/bin/env bash
# 호스트 서비스 3개를 띄운다. Ctrl+C 로 전부 종료.
#   review      :8790  결재 문서 API + 결재 웹 (브라우저: http://127.0.0.1:8790/)
#   knowledge   :8791  실무대장 stub
#   github-mcp  :8792  GitHub 채널 MCP (/github/mcp)
# 모두 127.0.0.1 에 뜨고, .env 에 RFA_SANDBOX_HOST(예: 172.18.0.1)가 있으면 샌드박스용으로 그 주소에도 뜬다
# (github-mcp 는 그 주소에서 HTTPS: certs/ 필요, scripts/make_certs.sh).
# 설정은 .env (없으면 cp .env.example .env). 로그는 $LOG_DIR (기본 data/state/logs).
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=${ENV_FILE:-.env}
LOG_DIR=${LOG_DIR:-data/state/logs}
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE — cp .env.example .env 후 값을 채우세요" >&2; exit 1; }
mkdir -p "$LOG_DIR"

# 이미 누가 쓰는 포트면 멈춘다 (예전 프로세스가 대신 응답해 확인을 통과해 버리는 것을 막음)
for port in 8790 8791 8792; do
  if curl -s -o /dev/null --max-time 1 "http://127.0.0.1:$port/"; then
    echo "포트 $port 가 이미 쓰이고 있어요. 예전 서버를 먼저 끄세요: lsof -i :$port" >&2
    exit 1
  fi
done

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

start() {
  local name=$1 port=$2
  uv run --env-file "$ENV_FILE" python -m rfa_hostserve "$name" --port "$port" >"$LOG_DIR/$name.log" 2>&1 &
  pids+=($!)
}
start review 8790
start knowledge 8791
start github-mcp 8792

# 응답이 오면 뜬 것 (MCP 는 bearer 없이 401 이 정상)
ready() { [ "$(curl -s -o /dev/null -w '%{http_code}' "$1")" = "$2" ]; }
for _ in $(seq 1 30); do
  for pid in "${pids[@]}"; do
    kill -0 "$pid" 2>/dev/null || { echo "서비스가 시작하다 종료됐어요. $LOG_DIR/*.log 를 보세요." >&2; exit 1; }
  done
  if ready http://127.0.0.1:8790/reviews 200 && ready http://127.0.0.1:8791/tasks 200 \
     && ready http://127.0.0.1:8792/github/mcp 401; then
    echo "ready: 결재 웹 http://127.0.0.1:8790/  (logs: $LOG_DIR)"
    wait
    exit 0
  fi
  sleep 1
done
echo "서비스가 30초 안에 뜨지 않았어요. $LOG_DIR/*.log 를 보세요." >&2
exit 1
