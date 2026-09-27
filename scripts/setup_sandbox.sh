#!/usr/bin/env bash
# 레포 파일만으로 RFA 샌드박스를 만든다 (기존 샌드박스는 건드리지 않음).
#   scripts/setup_sandbox.sh [phase...]     phase 를 안 주면 전부 순서대로
# phases:
#   create  nemoclaw onboard: 에이전트 매니페스트, 자체 CA 신뢰, workflow/·common/ 읽기 전용 마운트
#   policy  pypi(설치용) + 호스트 서비스 정책(policies/rfa-host.yaml, 결재 경로 제외)
#   install 샌드박스 안 venv 에 rfa-common, rfa-workflow 설치 + workflow.env 작성
#   mcp     github MCP 를 관리형 MCP 로 등록 (HTTPS, bearer 는 OpenShell 이 보관)
#   agent   public-desk 작업 폴더에 AGENTS.md 업로드 + exec 허용 목록
#   cron    public-desk 를 주기적으로 깨우는 cron (RFA_DESK_CRON, 기본 */5 * * * *)
#   check   완료 조건 확인
# 필요: .env (RFA_SANDBOX_HOST, ANTHROPIC_API_KEY, GITHUB_MCP_TOKEN, RFA_MODEL ...), certs/ (scripts/make_certs.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd -P)

set -a; . ./.env; set +a
SANDBOX=${RFA_SANDBOX:-rfa}
HOST_IP=${RFA_SANDBOX_HOST:?.env 에 RFA_SANDBOX_HOST 가 필요해요 (예: 172.18.0.1)}
REMOTE=/sandbox/rfa                 # workflow/, common/ 읽기 전용 마운트 (root 소유라 쓰기 불가)
VENV=/sandbox/rfa-venv
ENV_IN_SANDBOX=/sandbox/rfa-workflow.env
WORKSPACE=/sandbox/.openclaw/workspace-public-desk
MCP_URL="https://$HOST_IP:8792/github/mcp"

say() { printf '\n== %s\n' "$*"; }
nc() { nemoclaw "$SANDBOX" "$@" 2> >(grep -v -E 'UNDICI|trace-warnings' >&2); }
sx() { nc exec -- sh -lc "$1"; }

phase_create() {
  say "create: 샌드박스 $SANDBOX"
  [ -f certs/rfa-ca.pem ] || { echo "certs/rfa-ca.pem 없음 → RFA_SANDBOX_HOST=$HOST_IP scripts/make_certs.sh" >&2; exit 1; }
  # 중간에 끊긴 onboard 가 있으면 이어서 (RFA_ONBOARD_FRESH=1 이면 처음부터)
  local resume=()
  if nemoclaw list 2>/dev/null | grep -q "^    $SANDBOX  interrupted"; then
    [ "${RFA_ONBOARD_FRESH:-0}" = 1 ] && resume=(--fresh) || resume=(--resume)
  fi
  NEMOCLAW_NON_INTERACTIVE=1 NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 \
  NEMOCLAW_PROVIDER=anthropic NEMOCLAW_MODEL="${RFA_MODEL:-claude-opus-5}" \
  NEMOCLAW_WEB_SEARCH_PROVIDER=none \
  NEMOCLAW_CORPORATE_CA_BUNDLE="$ROOT/certs/rfa-ca.pem" \
    nemoclaw onboard --non-interactive --yes-i-accept-third-party-software "${resume[@]}" \
      --name "$SANDBOX" --agents agents/agents.yaml \
      --host-mount "$ROOT/workflow:$REMOTE/workflow" \
      --host-mount "$ROOT/common:$REMOTE/common"
}

phase_policy() {
  say "policy: pypi(설치용) + 호스트 서비스"
  nc policy add pypi --yes
  local tmp; tmp=$(mktemp --suffix=.yaml); sed "s/__HOST__/$HOST_IP/g" policies/rfa-host.yaml > "$tmp"
  nc policy add --from-file "$tmp" --trusted-private-host "$HOST_IP" --yes
  rm -f "$tmp"
}

phase_install() {
  say "install: $VENV 에 rfa-common, rfa-workflow"
  # 읽기 전용 마운트에서 바로 빌드하지 않도록 복사본으로 설치
  sx "set -e; rm -rf /tmp/rfa-src; mkdir -p /tmp/rfa-src; cp -r $REMOTE/common $REMOTE/workflow /tmp/rfa-src/;
      [ -x $VENV/bin/python ] || python3 -m venv $VENV;
      $VENV/bin/pip install -q --upgrade /tmp/rfa-src/common /tmp/rfa-src/workflow; rm -rf /tmp/rfa-src;
      $VENV/bin/rfa-workflow --help >/dev/null && echo installed"
  say "install: $ENV_IN_SANDBOX (권한 600)"
  local tmp; tmp=$(mktemp); chmod 600 "$tmp"
  cat > "$tmp" <<ENV
REVIEW_URL=http://$HOST_IP:8790
KNOWLEDGE_URL=http://$HOST_IP:8791
GITHUB_MCP_URL=$MCP_URL
GITHUB_MCP_TOKEN=$GITHUB_MCP_TOKEN
RFA_LLM_MODE=anthropic
ANTHROPIC_BASE_URL=https://inference.local
RFA_MODEL=${RFA_MODEL:-claude-opus-5}
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV
  nc upload "$tmp" "$ENV_IN_SANDBOX" >/dev/null
  rm -f "$tmp"
  sx "chmod 600 $ENV_IN_SANDBOX"
}

phase_mcp() {
  say "mcp: github → $MCP_URL (호스트 서비스가 떠 있어야 해요: scripts/run_services.sh)"
  nc mcp add github --url "$MCP_URL" --env GITHUB_MCP_TOKEN --trusted-private-host "$HOST_IP"
}

phase_agent() {
  say "agent: public-desk 프롬프트 + exec 허용 목록"
  sx "mkdir -p $WORKSPACE"
  nc upload agents/public-desk/AGENTS.md "$WORKSPACE/AGENTS.md" >/dev/null
  sx "openclaw approvals allowlist add --agent public-desk '$VENV/bin/rfa-workflow'"
}

phase_cron() {
  local expr=${RFA_DESK_CRON:-*/5 * * * *}
  say "cron: public-desk ($expr)"
  sx "openclaw cron add rfa-desk '새 멘션을 확인해' --agent public-desk --cron '$expr' --declaration-key rfa-desk"
}

phase_check() {
  say "check"
  nc agents list | grep -E "^- " || true
  nc mcp status github --tools || true
  sx "$VENV/bin/python - <<'PY'
import os, urllib.request, ssl
def code(method, url):
    req = urllib.request.Request(url, method=method, data=b'{}' if method == 'POST' else None,
                                 headers={'content-type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=10).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return type(e).__name__
print('inference.local       ', code('GET', 'https://inference.local/v1/models'))
print('review GET /reviews   ', code('GET', 'http://$HOST_IP:8790/reviews'))
print('review approve (막혀야)', code('POST', 'http://$HOST_IP:8790/reviews/1/approve'))
print('결재 웹 / (막혀야)     ', code('GET', 'http://$HOST_IP:8790/'))
print('knowledge /tasks      ', code('GET', 'http://$HOST_IP:8791/tasks'))
PY"
}

main() {
  local phases=("$@")
  [ ${#phases[@]} -gt 0 ] || phases=(create policy install mcp agent cron check)
  for p in "${phases[@]}"; do "phase_$p"; done
}
main "$@"
