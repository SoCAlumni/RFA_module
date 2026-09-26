# 셋업과 재현

## 전제

- nemoclaw v0.0.124 + OpenShell 설치, Docker 동작, `nemoclaw onboard` 1회 완료(inference provider 설정됨).
- Python 3.12+, `uv`.
- GitHub fine-grained 토큰: 감시할 테스트 레포 한정, Issues read/write (+ Pull requests read). notifications 권한은 필요 없음.

## 파일

```
.env.example → .env (gitignore)
  GITHUB_TOKEN=
  GITHUB_MCP_TOKEN=          # MCP 서버 bearer (임의 문자열)
  RFA_CLEARANCE_KEY=         # openssl rand -hex 32
  RFA_HOST_IP=172.17.0.1     # docker bridge, 샌드박스에서 호스트로 가는 주소
  REVIEW_URL=https://rfa-host.local:8790
  KNOWLEDGE_URL=https://rfa-host.local:8791
  RFA_MODEL=claude-opus-5
```

## 순서

```bash
# 1. 호스트 서비스
cp .env.example .env && $EDITOR .env
./scripts/run_services.sh          # review(8790) + knowledge_stub(8791) + mcp_channels(8792), TLS는 caddy/mkcert 프록시

# 2. 샌드박스 (레포 파일만으로 새로 만듦, 기존 my-assistant는 건드리지 않음)
./scripts/setup_sandbox.sh
#   nemoclaw onboard --name rfa --agents agents/agents.yaml --yes
#   nemoclaw rfa hosts-add rfa-host.local $RFA_HOST_IP
#   nemoclaw rfa policy add --from-file policies/rfa.yaml     # rfa-host.local:8790/8791/8792 허용
#   nemoclaw rfa mcp add github --url https://rfa-host.local:8792/github/mcp --env GITHUB_MCP_TOKEN --trusted-private-host rfa-host.local --deny-tool 'post_*'
#   nemoclaw rfa upload workflow/ /sandbox/workflow && nemoclaw rfa exec -- pip install -e /sandbox/workflow
#   (workflow.run stdio MCP 등록: openclaw config로)
#   cron 등록

# 3. 확인
nemoclaw rfa mcp status --tools
nemoclaw rfa agents list
nemoclaw rfa exec -- curl -s https://inference.local/v1/models | head -c 100     # 200
nemoclaw rfa exec -- curl -s -m 3 https://rfa-host.local:8790/reviews/1/approve  # 실패해야 정상

# 4. 데모
# 테스트 레포 이슈에 @mention 작성 → 2분 내 http://127.0.0.1:8790 알람 → 승인
# 즉시 트리거: nemoclaw rfa agent --agent public-desk -m "새 멘션이 있는지 확인해."
```

## 인증서 (mkcert)

```bash
mkcert -install
mkcert rfa-host.local 172.17.0.1
export NEMOCLAW_CORPORATE_CA_BUNDLE="$(mkcert -CAROOT)/rootCA.pem"   # onboard 전에
```
caddy(또는 uvicorn ssl)가 `rfa-host.local` 인증서로 8790/8791/8792를 서빙. approve/reject는 앱 레벨에서 `127.0.0.1` 출처만 허용하므로 프록시를 통해 와도 거부된다.

## 막혔을 때 (fallback)

| 문제 | 임시 대안 |
|---|---|
| `mcp add` 사설 호스트 검증 실패 | knowledge/review 호출은 정책 preset만으로 허용(LangGraph는 MCP가 아니라 HTTP 클라이언트), github MCP만 mcp add |
| 그래도 샌드박스→호스트 불가 | LangGraph를 호스트에서 실행(`python -m rfa_workflow run`), public-desk는 알림만. 데모 확보 후 이전 |
| `workflow.run` stdio 등록 불가 | public-desk에 exec 허용 + 스킬로 `python -m rfa_workflow run` |

## GitHub 공유

- 올림: 코드, docs, contracts, agents, policies, scripts, data/knowledge, data/policy(예시), `.env.example`.
- 안 올림: `.env`, `data/state/`, 인증서, 샌드박스/이미지.
