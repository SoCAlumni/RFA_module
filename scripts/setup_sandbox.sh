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
MCP_URL="https://$HOST_IP:8792/github/mcp"        # public-desk 에이전트 (nemoclaw 관리형 MCP)
DESK_MCP_URL="https://$HOST_IP:8792/github/desk/mcp"  # desk-once (python, policies/rfa-host.yaml)

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
  # TLS 신뢰는 샌드박스가 이미 준다: SSL_CERT_FILE=/tmp/nemoclaw-ca-bundle.pem (OpenShell CA + certs/rfa-ca.pem)
  local tmp; tmp=$(mktemp); chmod 600 "$tmp"
  cat > "$tmp" <<ENV
REVIEW_URL=http://$HOST_IP:8790
KNOWLEDGE_URL=http://$HOST_IP:8791
GITHUB_MCP_URL=$DESK_MCP_URL
GITHUB_MCP_TOKEN=$GITHUB_MCP_TOKEN
RFA_LLM_MODE=anthropic
ANTHROPIC_BASE_URL=https://inference.local
RFA_MODEL=${RFA_MODEL:-claude-opus-5}
ENV
  nc upload "$tmp" "$ENV_IN_SANDBOX" >/dev/null
  rm -f "$tmp"
  sx "chmod 600 $ENV_IN_SANDBOX"
}

phase_mcp() {
  say "mcp: github → $MCP_URL (호스트 서비스가 떠 있어야 해요: scripts/run_services.sh)"
  # list_mentions 는 에이전트에게서 막는다: 멘션을 가져오는 건 결정적 코드(desk-once)만 한다.
  # 에이전트가 직접 부르면 멘션이 "본 것"으로 소비돼 워크플로로 가지 못한다.
  # 이 차단은 /github/mcp 경로 전체에 걸리므로 desk-once 는 /github/desk/mcp 를 쓴다.
  if nc mcp list 2>/dev/null | grep -q "github"; then
    nc mcp update github --deny-tool list_mentions
  else
    nc mcp add github --url "$MCP_URL" --env GITHUB_MCP_TOKEN --trusted-private-host "$HOST_IP" \
      --deny-tool list_mentions
  fi
}

phase_agent() {
  say "agent: public-desk 프롬프트 + exec 허용 목록"
  # upload 의 대상은 디렉터리로 준다 (파일 경로를 주면 같은 이름의 디렉터리가 생길 수 있음)
  sx "mkdir -p $WORKSPACE; [ -d $WORKSPACE/AGENTS.md ] && rm -rf $WORKSPACE/AGENTS.md; true"
  nc upload agents/public-desk/AGENTS.md "$WORKSPACE/" >/dev/null
  sx "test -f $WORKSPACE/AGENTS.md && echo 'AGENTS.md ok'"
  sx "openclaw approvals allowlist add --agent public-desk '$VENV/bin/rfa-workflow'"
}

phase_cron() {
  local expr=${RFA_DESK_CRON:-*/5 * * * *}
  say "cron: public-desk ($expr)"
  # openclaw cron 은 샌드박스 CLI 장치에 operator.admin 이 있어야 한다 (my-assistant 의 CLI 와 같은 상태).
  # 처음엔 승격 요청이 대기로 남는다. 사람이 확인하고 승인한 뒤 이 단계를 다시 돌린다.
  sx "openclaw cron add --name rfa-desk --message '새 멘션을 확인해' --agent public-desk --cron '$expr' --declaration-key rfa-desk" || {
    echo "cron 등록 실패. CLI 장치 권한 승격(operator.admin) 요청을 확인하고 승인한 뒤 다시 실행하세요:" >&2
    echo "  nemoclaw $SANDBOX exec -- openclaw devices list" >&2
    echo "  nemoclaw $SANDBOX exec -- openclaw devices approve <requestId>" >&2
    echo "  scripts/setup_sandbox.sh cron" >&2
    return 1
  }
}

phase_check() {
  say "check"
  nc agents list | grep -E "^- " || true
  nc mcp status github --tools || true
  sx "$VENV/bin/python - <<'PY'
import urllib.request
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
print('desk MCP (bearer 없음 401)', code('POST', '$DESK_MCP_URL'))
print('에이전트 MCP 경로 (막혀야)', code('POST', '$MCP_URL'))
PY"
}

main() {
  local phases=("$@")
  [ ${#phases[@]} -gt 0 ] || phases=(create policy install mcp agent cron check)
  for p in "${phases[@]}"; do "phase_$p"; done
}
main "$@"
