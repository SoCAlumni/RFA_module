# v1 → v2 정리 내역 (Step 2, 2026-09-27)

9/27 팀 회의(`reference/927.md`)로 설계와 역할이 바뀌어, 9/26 에 만든 v1 코드(Step 1~9)를 정리했다.
**v1 마지막 상태는 커밋 `819053f`** (Step 9, PR #11). 지운 코드가 필요하면 `git show 819053f:<경로>` 로 꺼낸다.

## 왜 바뀌었나

| | v1 (9/26) | v2 (9/27) |
|---|---|---|
| 이 레포 범위 | 외부 요청 대응 + **기밀검토** + 결재 + 게시 | 대응 에이전트 + 채널(GitHub·Slack) + **결재 백엔드** |
| 기밀 판단 | 이 레포 (scanner, censor, policy) | **민섭님 head agent** 안에서. 우리는 검열된 지식만 받음 |
| 지식 출처 | 실무대장 `GET /tasks`, `POST /tasks/{id}/ask`. 업무는 우리가 고름 | head agent `POST /ask` 하나. 업무는 head 가 고름 |
| 결재 화면 | 이 레포 (review 서비스 내장 웹) | **다영님 프런트엔드**. 우리는 API(`/approvals`) 제공 + 참조 웹 |
| 거절 후 | 종착 (피드백은 censor few-shot 으로) | 사유 + 이전 초안을 head 에 다시 보내 재작성 → 재결재 (최대 3회) |
| 실행 위치 | 샌드박스 안 (OpenClaw public-desk + MCP) | 호스트 (desk 상주 프로세스). 샌드박스는 기밀 영역만 (민섭·다영) |
| 게시 보호 | HMAC 서명 토큰 + loopback 전용 승인 | 없음. 승인 호출 차단은 샌드박스 정책(다영님) |
| 채널 | GitHub 만 | GitHub + Slack (Socket Mode) |

## 지운 것

| 경로 | 무엇이었나 | 이유 |
|---|---|---|
| `services/review/` 전체 | 결재 문서 API, 상태기계, 스캐너, 정책, HMAC clearance, 게시자, 결재 웹 | 검열·정책은 민섭님으로 이관. 결재는 v2 모델(`approvals`)로 Step 3 에서 새로 (store 골격과 웹은 여기서 가져옴) |
| `services/mcp_channels/server.py` | GitHub 읽기 MCP 서버 | 에이전트가 샌드박스 밖에서 돌아 MCP 를 거칠 이유가 없음 |
| `contracts/review.openapi.yaml`, `contracts/knowledge.openapi.yaml` | v1 계약 | `approvals`, `head` 계약으로 대체 (Step 1) |
| `common/rfa_common/models.py` | v1 공용 모델 | `common/rfa_common/contracts.py` 로 대체 (Step 1) |
| `data/policy/` | 기밀 기준 문서, 내부 호스트·경로 목록 | 검열은 민섭님 소관 |
| `workflow/rfa_workflow/` 의 `graph_public.py`, `nodes/`, `desk.py`, `recovery.py`, `mcp_entry.py` | v1 Public 대응 그래프, 노드, 호스트 데스크, 복구 예산, OpenClaw 용 MCP 진입점 | 흐름이 바뀜 (업무 선택·첨삭·censor 제거, 결재 루프 추가). Step 5·6 에서 새로 |
| `workflow/rfa_workflow/` 의 `cli.py`, `clients.py`, `deps.py`, `state.py`, `llm.py`, `__main__.py`, `prompts/` | CLI, 서비스 클라이언트, 의존성 묶음, 그래프 상태, LLM 계층, 프롬프트 | 전부 지운 모듈을 import 해서 깨진 상태. Step 5 에서 새로 (아래 "다시 쓸 것") |
| `services/tests/` 의 v1 테스트, `workflow/tests/` 전체 | | 대상 코드가 없어짐. 남길 부분은 새 테스트로 옮김 |
| `docs/modules/` | v1 모듈별 문서 | `docs/architecture.md`, `docs/contracts.md` 로 대체 |
| `scripts/demo_host.sh` | v1 호스트 데모 | Step 6 에서 `scripts/run_desk.sh` 로 |

## 옮긴 것

| v1 | v2 | 바뀐 점 |
|---|---|---|
| `services/knowledge_stub/` | `services/head_stub/` | `GET /tasks`, `POST /tasks/{id}/ask` → `POST /ask` 하나. 업무를 stub 이 고르고, 거절 사유 키워드가 든 문장을 뺀다. `Doc` 에 `task_id` 추가, 아무도 안 읽던 `summary`·`tags`·`source_line` 제거 (응답에 sources 없음) |
| `services/mcp_channels/` | `services/channels/` | `github.py` 는 import 와 채널 이름(`public` → `github`)만. Slack·공통 인터페이스는 Step 4 |
| `services/tests/test_knowledge_stub.py` | `services/tests/test_head_stub.py` | loader·rank 테스트 유지, `/ask` 테스트 추가 |
| `services/tests/test_github.py` | 같은 이름, 새로 씀 | 멘션 규칙·추적·헤더·스레드·게시 테스트 유지. MCP 서버·서명 게시자 테스트 제거 |

## 남긴 것 (그대로)

`services/head_stub/rank.py`(import 한 줄만), `services/tests/fake_github.py`, `data/knowledge/`, `scripts/build_api_docs.sh`, `.github/workflows/api-docs.yml`, `certs/`(gitignore).

## 다시 쓸 것 — v1 에서 가져올 코드

| 단계 | 가져올 것 (`git show 819053f:<경로>`) |
|---|---|
| Step 3 approvals | `services/review/store.py` — 파일 하나 = 문서 하나, tmp+rename 원자적 쓰기, 전이표, 멱등 create. `services/review/static/index.html` — 결재 웹 골격 |
| Step 5 workflow | `workflow/rfa_workflow/llm.py` — `AnthropicLLM`(structured outputs, 거절·잘림 처리), `prompt()`, `make_llm`. `clients.py` 의 `_Service._request` — 연결 오류·429·5xx 지수 backoff 재시도. `prompts/writer.md`. `workflow/tests/wf_support.py` 의 `FakeLLM`, `FlakyHttp` |
| Step 6 desk | `scripts/run_services.sh` 의 포트 점검·준비 확인 패턴 (Step 2 에서 head_stub 용으로 이미 축소해 둠) |

## 설정 (.env) 변화

| 키 | v2 |
|---|---|
| `RFA_CLEARANCE_KEY`, `GITHUB_MCP_TOKEN`, `GITHUB_MCP_URL`, `RFA_MCP_ALLOWED_HOSTS`, `REVIEW_URL`, `KNOWLEDGE_URL`, `RFA_SANDBOX*`, `RFA_TLS_*`, `RFA_DESK_CRON` | 없어짐. 기존 `.env` 에 남아 있어도 무시됨 |
| `RFA_DATA_DIR`, `GITHUB_TOKEN`, `RFA_GITHUB_LOGIN`, `RFA_GITHUB_REPOS` | 유지 |
| `HEAD_URL`, `APPROVALS_URL`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `RFA_CHANNELS`, `RFA_PUBLISHER`, `RFA_CORS_ORIGINS`, `RFA_LLM_MODE`, `RFA_MODEL`, `ANTHROPIC_API_KEY` | 그 키를 읽는 코드가 들어오는 단계(3~6)에서 `.env.example` 에 추가 |
