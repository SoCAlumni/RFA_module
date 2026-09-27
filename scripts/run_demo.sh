#!/usr/bin/env bash
# 데모용 원샷 실행: 예전 프로세스를 정리하고 서비스 + desk 를 한 번에 띄운다. Ctrl+C 로 전부 종료.
#   1) 남아 있는 desk 루프 / 8790·8791 리스너를 먼저 끈다 (중복 실행 방지)
#   2) scripts/run_services.sh 를 백그라운드로 띄우고 ready 를 기다린다
#   3) desk 를 포그라운드로 띄운다. desk 가 죽거나 Ctrl+C 면 서비스도 같이 내린다
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=${ENV_FILE:-.env}
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE — cp .env.example .env 후 값을 채우세요" >&2; exit 1; }

# 1) 예전 프로세스 정리
pkill -f 'rfa_workflow desk' 2>/dev/null && echo "예전 desk 루프를 종료했어요" || true
for port in 8790 8791; do
  pids=$(lsof -t -i ":$port" -sTCP:LISTEN 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "포트 $port 의 예전 서버($pids)를 종료했어요"
    kill $pids 2>/dev/null || true
  fi
done
# 종료가 반영될 때까지 잠깐 기다린다
for _ in $(seq 1 10); do
  lsof -t -i :8790 -i :8791 -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 1
done

# 2) 서비스 기동
scripts/run_services.sh &
svc_pid=$!
cleanup() { kill "$svc_pid" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM

ready() { [ "$(curl -s -o /dev/null -w '%{http_code}' "$1")" = "200" ]; }
for _ in $(seq 1 30); do
  kill -0 "$svc_pid" 2>/dev/null || { echo "서비스가 시작하다 종료됐어요. 로그를 보세요." >&2; exit 1; }
  ready http://127.0.0.1:8790/approvals && ready http://127.0.0.1:8791/openapi.json && break
  sleep 1
done
ready http://127.0.0.1:8790/approvals || { echo "서비스가 30초 안에 뜨지 않았어요." >&2; exit 1; }

# 3) desk 기동 (포그라운드)
echo "결재 웹: http://127.0.0.1:8790/  — desk 를 시작합니다. Ctrl+C 로 전부 종료."
uv run --env-file "$ENV_FILE" python -m rfa_workflow desk "$@"
