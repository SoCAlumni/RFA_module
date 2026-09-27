# 셋업과 재현

## 전제

- nemoclaw v0.0.124 + OpenShell 설치, Docker 동작, `nemoclaw onboard` 1회 완료(inference provider 설정됨).
- Python 3.12+, `uv`.
- GitHub fine-grained 토큰: 감시할 테스트 레포 한정, Issues read/write (+ Pull requests read). notifications 권한은 필요 없음.

## 파일

`.env.example` → `.env` (gitignore). 항목별 설명은 `.env.example` 주석. 샌드박스에 필요한 것:

```
RFA_SANDBOX_HOST=172.18.0.1        # OpenShell docker 네트워크의 호스트 IP
RFA_MCP_ALLOWED_HOSTS=127.0.0.1:*,localhost:*,172.18.0.1:*
RFA_TLS_CERT=certs/host.pem        # scripts/make_certs.sh 결과
RFA_TLS_KEY=certs/host.key
RFA_SANDBOX=rfa
RFA_DESK_CRON="*/5 * * * *"
```

`RFA_SANDBOX_HOST` 찾기: `docker network inspect openshell-docker -f '{{(index .IPAM.Config 0).Gateway}}'`

## 순서

```bash
# 1. 호스트 서비스
cp .env.example .env && $EDITOR .env
./scripts/make_certs.sh            # certs/rfa-ca.pem + certs/host.pem (IP:$RFA_SANDBOX_HOST). 샌드박스 쓸 때만
./scripts/run_services.sh          # review(8790, 결재 웹) + knowledge_stub(8791) + github MCP(8792). Ctrl+C 로 종료
                                   # 127.0.0.1 과 RFA_SANDBOX_HOST 둘 다에 뜬다 (github MCP 는 샌드박스 쪽이 HTTPS)
                                   # 포트가 이미 쓰이면 바로 멈춘다. 로그: data/state/logs/

# 1-1. 샌드박스 없이 호스트에서 전체 흐름 (Step 9)
RFA_LLM_MODE=anthropic ./scripts/demo_host.sh   # 새 멘션 → 워크플로 → 결재 대기. 브라우저 http://127.0.0.1:8790/ 에서 승인
# 키 없이 흐름만 보려면 RFA_LLM_MODE=mock

# 2. 샌드박스 (레포 파일만으로 새로 만듦, 기존 my-assistant는 건드리지 않음)
./scripts/setup_sandbox.sh         # 전체. 단계만: ./scripts/setup_sandbox.sh policy install
#   create  onboard: agents/agents.yaml, workflow/·common/ 읽기 전용 마운트, CA = certs/rfa-ca.pem
#           (중단되면 다음 실행이 이어서 한다. 새로: RFA_ONBOARD_FRESH=1)
#   policy  pypi + policies/rfa-host.yaml (approve/reject·결재 웹은 없음)
#   install /sandbox/rfa-venv 에 워크플로, 설정은 /sandbox/rfa-workflow.env (600)
#   mcp     관리형 MCP github (https://$RFA_SANDBOX_HOST:8792/github/mcp), 에이전트에게 list_mentions 거부
#   agent   public-desk AGENTS.md + exec 허용 목록(rfa-workflow 하나)
#   cron    public-desk 를 RFA_DESK_CRON 마다 깨움 (아래 "cron 권한" 참고)
#   check   아래 3번 값 출력

# 3. 확인 (./scripts/setup_sandbox.sh check)
#   inference.local 200 / review GET 200 / approve 403 / 결재 웹 403 / knowledge 200
#   desk MCP(bearer 없음) 401 / 에이전트 MCP 경로를 python 이 쓰면 403

# 4. 데모
# 테스트 레포 이슈에 @mention 작성 → cron 주기 안에 http://127.0.0.1:8790 알람 → 승인
# 즉시 트리거: nemoclaw rfa agent --agent public-desk -m "새 멘션을 확인해"
```

## 호스트 방화벽

샌드박스 → 호스트 연결이 시간 초과면 호스트 방화벽이 docker 브리지를 막는 것이다 (UFW 기본 정책). 필요한 포트만 연다:

```bash
ip -o addr show | grep "$RFA_SANDBOX_HOST"      # 브리지 이름 (예: br-bfba6e839d6c)
sudo ufw allow in on <브리지> from 172.18.0.0/16 to "$RFA_SANDBOX_HOST" port 8790:8792 proto tcp
```

approve/reject 는 앱이 `127.0.0.1` 출처만 받으므로 이 포트가 열려도 샌드박스에서 결재할 수 없다 (정책에서도 경로가 없다).

## 인증서

`scripts/make_certs.sh` 가 로컬 CA 와 `IP:$RFA_SANDBOX_HOST` 서버 인증서를 `certs/`(gitignore)에 만든다. CA 는 onboard 때 `NEMOCLAW_CORPORATE_CA_BUNDLE` 로 들어가 샌드박스의 `SSL_CERT_FILE`(/tmp/nemoclaw-ca-bundle.pem)에 합쳐진다. CA 파일이 group/world 쓰기 가능이면 nemoclaw 가 거부해 onboard 가 `managed image catalog ... failed validation` 으로 실패한다 (스크립트가 644 로 둔다). CA 를 바꾸면 onboard 를 다시 해야 한다.

## cron 권한

`openclaw cron add` 는 샌드박스 CLI 장치에 `operator.admin` 이 있어야 한다. 새 샌드박스에서는 첫 호출이 승격 요청을 남기고 실패한다. 요청 내용(clientId `cli`, scopes `operator.admin`)을 확인하고 승인한 뒤 다시 돌린다:

```bash
nemoclaw rfa exec -- openclaw devices list
nemoclaw rfa exec -- openclaw devices approve <requestId>
./scripts/setup_sandbox.sh cron
```

public-desk 는 CLI 를 쓸 수 없다 (fs 툴 거부, exec 는 `rfa-workflow` 하나만 허용).

## 알려진 제약

| 증상 | 이유 / 대응 |
|---|---|
| `mcp status github --tools` 의 tool discovery 실패 (`no valid managed endpoint`) | nemoclaw 상태 점검이 사설 IP 엔드포인트를 검사하지 못함. 런타임 호출은 된다 |
| 에이전트가 AGENTS.md 를 무시 | 업로드가 디렉터리로 들어간 경우. `agent` 단계가 대상 디렉터리로 올리고 파일인지 확인한다 |
| desk-once 의 list_mentions 403 | `/github/mcp` 는 에이전트용(list_mentions 거부). desk-once 는 `/github/desk/mcp` 를 써야 한다 (`install` 이 설정) |
| 샌드박스→호스트 불가 | LangGraph를 호스트에서 실행(`scripts/demo_host.sh`) |

## GitHub 공유

- 올림: 코드, docs, contracts, agents, policies, scripts, data/knowledge, data/policy(예시), `.env.example`.
- 안 올림: `.env`, `data/state/`, 인증서, 샌드박스/이미지.
