# 개발 계획 (단계별)

## 규칙

- **한 단계 = 재현 가능한 기능 하나 = PR 하나.** 리뷰·머지 전에는 다음 단계를 시작하지 않는다.
- 브랜치 `step-NN-<name>`, PR 제목 `Step NN: <기능>`. PR 본문에 재현 명령과 테스트 결과를 붙인다.
- 단계마다 테스트를 작성하고 `uv run pytest`가 통과해야 한다.
- 셀프 코드리뷰 체크리스트 (PR 전에 확인, PR 본문에 결과 기재):
  - [ ] dead code 없음: 미사용 import, 함수, 파일, 설정 키, 주석 처리된 코드
  - [ ] 모든 공개 함수/엔드포인트에 동작을 증명하는 테스트가 있음
  - [ ] 실패 경로(잘못된 입력, 순서 위반, 인증 실패)도 테스트됨
  - [ ] `docs/modules/*.md`와 코드가 일치함. 달라졌으면 문서를 고침
  - [ ] 비밀값이 코드/커밋에 없음
  - [ ] 이 단계 범위 밖 코드를 미리 넣지 않음
- 공통 도구: Python 3.12+, `uv`, `pytest`, `ruff`(lint + format). FastAPI, pydantic v2, httpx, FastMCP, LangGraph.

## 진행 표

| Step | 기능 | 상태 |
|---|---|---|
| 0 | 초기 커밋 (docs, README, .gitignore) | 완료 |
| 1 | 스켈레톤 + 계약 + 공용 모델 | 완료 (PR #1) |
| 2 | knowledge stub + 데모 데이터 | 완료 (PR #2) |
| 3 | review 코어 (상태기계, reviews API) | 완료 (PR #5) |
| 4 | scanner + policy | 완료 (PR #6) |
| 5 | clearance + 결재 (approve/reject) | 완료 (PR #7) |
| 6 | 알람/결재 웹 | 완료 (PR #8) |
| 7 | github MCP | 완료 (PR #9) |
| 8 | rfa_workflow (LangGraph) | 완료 (PR #10) |
| 9 | 호스트 E2E | 완료 (PR #11) |
| 10 | 샌드박스 재현 | PR #12 리뷰 중 |
| 11 | 샌드박스 E2E + README | |
| 12 | 멘션 대기열과 복구 (at-least-once 처리) | Step 11 뒤, 본선 전 |

---

## Step 1: 스켈레톤 + 계약 + 공용 모델

**목표.** 모든 모듈이 공유하는 뼈대. 이후 단계가 같은 패키지 구조, 같은 모델, 같은 테스트 러너를 쓴다.

**범위.**
- 포함: uv 워크스페이스, `rfa_common` 패키지(pydantic 모델), OpenAPI 계약 파일, `.env.example`, ruff/pytest 설정
- 제외: 서비스 코드, 실행 스크립트

**만들 파일.**
```
pyproject.toml                 # uv workspace root: ruff, pytest 설정
services/pyproject.toml        # 패키지 rfa_services (rfa_common, 이후 knowledge_stub/review/mcp_channels)
services/rfa_common/__init__.py
services/rfa_common/models.py  # 아래 모델
services/tests/test_models.py
contracts/knowledge.openapi.yaml
contracts/review.openapi.yaml
.env.example
```

**동작 정의.** `rfa_common/models.py`:
| 모델 | 필드 |
|---|---|
| `Channel` | enum `public`, `internal` |
| `KnowledgeResult` | task_id, answer, confidence(0~1), sources[str] (근거 한 줄씩) |
| `TaskInfo` | id, name, description, updated_at |
| `Mention` | channel, target(`owner/repo#N` 형식 검증), author, text, url, created_at |
| `ReviewStatus` | enum opened, knowledge_ready, drafted, scanned, reviewed, approved, rejected, posted, needs_human |
| `EditVerdict` | round(int≥1), verdict(pass/revise), notes?, issues[] |
| `ScanHit` | type(enum token/private_ip/internal_host/internal_path), match, span[start,end] |
| `VerdictReason` | rule(`scope:id` 형식), span, action(remove/blur/keep) |
| `Verdict` | verdict(allow/redact/block), redacted_body?, reasons[], summary. redact면 redacted_body 필수 |
| `Event` | at(datetime), who, what, detail? |
| `Review` | id, status, channel, target, source_url, requester, question, knowledge?, draft?, edit_log[], scan[], verdict?, final_body?, decision?, events[] |

**테스트.**
- 각 모델 정상 생성 1건
- `Mention.target` 형식 오류 거부
- `Verdict(redact)`에 redacted_body 없으면 거부
- `KnowledgeResult.confidence` 범위 밖 거부
- `VerdictReason.rule` 형식 오류 거부

**완료 조건.**
```bash
uv sync && uv run ruff check . && uv run pytest
```

---

## Step 2: knowledge stub + 데모 데이터

**목표.** 실무대장 계약(`contracts/knowledge.openapi.yaml`)을 그대로 지키는 stub 서비스. LangGraph가 붙을 대상 1호.

**범위.** 포함: FastAPI 앱, md 로더, 키워드 랭킹, 데모 데이터 3 task. 제외: LLM 호출, 실무대장 실제 연동.

**만들 파일.**
```
services/knowledge_stub/__init__.py
services/knowledge_stub/app.py      # GET /tasks, POST /tasks/{id}/ask
services/knowledge_stub/loader.py   # data/knowledge/<task>/_task.yaml + *.md(frontmatter)
services/knowledge_stub/rank.py     # 질문·문서 토큰 겹침 점수, 상위 k
services/tests/test_knowledge_stub.py
data/knowledge/orbit/_task.yaml, progress-2026-09.md
data/knowledge/quantization/_task.yaml, qat-notes.md
data/knowledge/prism/_task.yaml, design.md
```

**동작 정의.**
- `GET /tasks` → `TaskInfo[]` (폴더 스캔, `_task.yaml` 없는 폴더는 무시)
- `POST /tasks/{id}/ask {question}` → `KnowledgeResult`. 없는 task는 404. 상위 k=3 문서 본문을 이어 붙여 answer, 겹침 비율을 confidence, 선택 문서를 `"제목: 요약"` 문자열로 sources.
- 데모 데이터에 일부러 넣는 기밀: orbit에 미공개 모델명 `Nimbus2`, 수치 `EM 0.5%p`, 릴리즈 `11/3`, GPU pool `10.12.3.4`, 토큰 `hf_…`; prism에 경로 `/nfs/prism/`. quantization은 깨끗(대조군).
- 데이터 경로는 env `RFA_DATA_DIR`(기본 `./data`).

**테스트.** `/tasks` 3건, `ask`가 orbit 질문에 progress 문서를 sources로 반환, 없는 task 404, frontmatter 누락 md는 건너뜀.

**완료 조건.**
```bash
uv run uvicorn knowledge_stub.app:app --port 8791 &
curl -s localhost:8791/tasks | jq
curl -s -X POST localhost:8791/tasks/orbit/ask -H 'content-type: application/json' -d '{"question":"ORBIT 벤치마크 진행 어때?"}' | jq
uv run pytest
```

---

## Step 3: review 코어 (상태기계, reviews API)

**목표.** 결재 문서의 생성과 전이. 순서 위반은 409. 스캔·서명·결재는 아직 없음.

**범위.** 포함: 파일 저장소, transition, reviews 엔드포인트(approve/reject 제외), events. 제외: scanner, policy, clearance, 웹.

**만들 파일.**
```
services/review/__init__.py
services/review/app.py         # 라우트
services/review/store.py       # data/state/review-<id>.json, 전이표 ALLOWED, advance(), ReviewNotFound/InvalidTransition
services/rfa_common/models.py  # 요청 본문 모델 추가: OpenReviewRequest, DraftRequest, NeedsHumanRequest
services/tests/test_review_core.py
```

**동작 정의.**
| 엔드포인트 | 전이 | 본문 |
|---|---|---|
| `POST /reviews` | → opened | channel, target, source_url, requester, question |
| `GET /reviews?status=` | — | Review 목록 (id 순) |
| `GET /reviews/{id}` | — | Review 전체, 없으면 404 `{error: not_found, id}` |
| `POST /reviews/{id}/knowledge` | opened → knowledge_ready | KnowledgeResult |
| `POST /reviews/{id}/draft` | knowledge_ready → drafted | text, edit_log[] (Step 4에서 scanned까지 자동 전이 추가) |
| `POST /reviews/{id}/verdict` | drafted → reviewed (Step 4 이후 scanned → reviewed) | Verdict |
| `POST /reviews/{id}/needs-human` | 어느 상태(approved/posted 제외) → needs_human | reason |
- 모든 전이는 `events[]`에 `{at, who, what, detail}` 추가. `who`는 요청 헤더 `X-RFA-Actor`(기본 "unknown").
- 저장은 파일 단위 원자적 쓰기(tmp → rename).

**테스트.** 정상 경로 opened→…→reviewed, 각 잘못된 전이 409, 404, events 길이, 목록 status 필터, 재시작 후 로드.

**완료 조건.** `uv run pytest`, `uvicorn --factory review.app:create_app --port 8790` 후 (RFA_CLEARANCE_KEY 필요) curl로 opened→reviewed 재현.

---

## Step 4: scanner + policy

**목표.** 비밀값 자동 스캔과 기밀 기준 제공.

**범위.** 포함: scanner, `/draft`에서 자동 스캔 후 `scanned` 전이, policy 파일과 `GET /policy/{scope}`, feedback 읽기. 제외: feedback 쓰기(Step 5), personal 추가 API(9/28).

**만들 파일.**
```
services/review/scanner.py
services/review/policy.py
data/policy/public/official.md, personal.md
data/policy/internal_hosts.txt, internal_paths.txt
(data/policy/feedback.jsonl 은 런타임 파일이라 gitignore, 없으면 빈 목록)
services/tests/test_scanner.py, test_policy.py
```

**동작 정의.**
- `scan(text) -> ScanHit[]`: token(hf_/ghp_/github_pat_/sk-/AKIA/xox[bp]-), private_ip(RFC1918), internal_host(목록), internal_path(목록 접두사). 겹치는 hit는 긴 것 우선.
- `/draft`: drafted 기록 → scan → scanned 기록(events 2건). 두 전이는 `store.advance(*steps)`로 한 번에 저장.
- `GET /policy/{scope}` → `{scope, official: str, personal: str, feedback: FeedbackItem[]}`. scope는 public|internal, 없는 scope 404. feedback은 scope 일치 최근 10건, reject 우선.

**테스트.** 각 패턴 검출/비검출, 겹침 처리, `/draft` 후 status=scanned & scan 채워짐, policy 응답, 없는 scope 404.

---

## Step 5: clearance + 결재

**목표.** 사람 결재와 서명 토큰. 게이트 완성.

**범위.** 포함: HMAC 서명/검증, approve/reject, loopback 가드, publisher 인터페이스(이 단계는 `MockPublisher`가 기록만), reject → feedback.jsonl. 제외: GitHub 실제 게시(Step 7).

**만들 파일.**
```
services/review/clearance.py   # sign(review_id, target, body) -> token, verify(token, target, body)
services/review/publisher.py   # Publisher 프로토콜, MockPublisher
services/tests/test_clearance.py, test_approval.py
```

**동작 정의.**
- `POST /reviews/{id}/approve`: 클라이언트 주소가 127.0.0.1이 아니면 403. status≠reviewed면 409. verdict=block이면 409. final_body에 token hit 남아 있으면 409. → approved 기록 → token 발급 → publisher.publish(target, final_body, token) → posted 기록. 응답 `{status, posted_url?}`.
- `POST /reviews/{id}/reject {reason}`: reviewed → rejected, feedback.jsonl에 추가.
- `final_body` = verdict.redact ? redacted_body : draft.
- token: `base64url(json payload).base64url(hmac_sha256)`, exp 기본 10분. 키 `RFA_CLEARANCE_KEY` 없으면 앱 시작 실패.

**테스트.** 서명 왕복, 위조/만료/target 불일치/본문 변경 거부, 비-loopback 403, block 409, 순서 409, approve 후 posted & publisher 호출 1회, reject 후 feedback 1건 추가.

---

## Step 6: 알람/결재 웹

**목표.** 사람이 보는 화면. 실제 데이터로 목록·타임라인·결재.

**범위.** 포함: `static/index.html` + 인라인 JS, `GET /` 서빙. 제외: 편집 후 승인(9/27), personal 기준 UI.

**동작 정의.** 3초 폴링 `GET /reviews`. 좌측 목록(상태 배지, 새 항목 강조), 우측 타임라인(질문 → 지식 → 초안 → 첨삭 → 스캔 하이라이트 → 판정 원본↔수정안 + 사유 태그 → 버튼). 승인/거절(사유 필수) → API 호출 → 결과 반영.

**테스트.** `GET /`가 200 + HTML, 정적 파일 경로. 화면 동작은 수동 체크리스트(PR에 스크린샷).

---

## Step 7: github MCP

**목표.** 외부 채널 1호. 읽기는 MCP 툴, 게시는 내부 함수.

**만들 파일.**
```
services/mcp_channels/__init__.py, server.py, github.py, models.py
services/review/publisher.py   # GithubPublisher 추가
services/tests/test_github.py  # httpx MockTransport
```

**동작 정의.**
- MCP(streamable-http, `/github/mcp`, bearer `GITHUB_MCP_TOKEN`): `list_mentions(since?) -> Mention[]`(감시 레포 `RFA_GITHUB_REPOS`의 이슈 본문·댓글에서 `@RFA_GITHUB_LOGIN` 검색, 본 것은 `data/state/mentions_seen.json`), `get_thread(target) -> Thread`. notifications API는 fine-grained 토큰 미지원이라 쓰지 않음.
- 내부: `post_comment(target, body, token)`: `clearance.verify` → `POST /repos/{o}/{r}/issues/{n}/comments`. 실패 시 예외.
- review 앱은 env `RFA_PUBLISHER=github|mock`으로 publisher 선택.

**테스트.** mock transport로 세 함수, bearer 없으면 401, verify 실패 시 API 호출 없음. 테스트 레포 실호출은 PR 본문에 결과 기록.

---

## Step 8: rfa_workflow (LangGraph)

**목표.** 멘션 → 결재 대기까지의 고정 그래프. mock LLM으로 검증.

**만들 파일.**
```
workflow/pyproject.toml
workflow/rfa_workflow/{state,graph_public,llm,clients,cli,mcp_entry}.py
workflow/rfa_workflow/nodes/{intake,knowledge,press,submit,censor}.py
workflow/rfa_workflow/prompts/{pick_task,writer,editor,style_public,censor_public}.md
common/rfa_common/  # services 에서 분리: 워크플로(샌드박스)가 서비스 패키지를 끌고 가지 않게
workflow/tests/test_graph.py, fixtures/
```

**동작 정의.** `docs/modules/workflow.md`의 노드 표. `RFA_LLM_MODE=mock|anthropic`, `ANTHROPIC_BASE_URL`(샌드박스는 inference.local), `REVIEW_URL`, `KNOWLEDGE_URL`. 409 수신 시 needs_human.

**테스트.** pass 경로 최종 status=reviewed, revise 2회 상한, task 없음 → needs_human, review 409 → needs_human, verdict JSON 파싱 실패 1회 재시도.

---

## Step 9: 호스트 E2E

**목표.** 샌드박스 없이 전체 흐름. `scripts/run_services.sh`(서비스 3개), `scripts/demo_host.sh`(= `python -m rfa_workflow desk-once`: GitHub MCP 의 새 멘션 → 워크플로). public-desk 역할을 호스트 데스크(`rfa_workflow/desk.py`)가 대신한다. 실제 LLM은 호스트 `ANTHROPIC_API_KEY`.

**완료 조건.** 테스트 이슈 멘션 → 알람 → 승인 → 수정본 게시. PR에 로그와 스크린샷.

---

## Step 10: 샌드박스 재현

**목표.** 레포 파일만으로 `rfa` 샌드박스를 만들고, 그 안의 public-desk 가 워크플로를 돌려 결재 대기까지 간다. `my-assistant` 는 건드리지 않는다.

**만든 것.**
- `services/rfa_hostserve`: 호스트 서비스를 127.0.0.1 과 `RFA_SANDBOX_HOST`(샌드박스 네트워크 쪽 호스트 IP, 예 172.18.0.1)에 함께 띄운다. 같은 앱 객체라 상태가 한 곳. github-mcp 의 샌드박스 바인드는 HTTPS 만(관리형 MCP 요구). `scripts/run_services.sh` 가 이걸로 띄운다.
- `scripts/make_certs.sh`: 로컬 CA(`certs/rfa-ca.pem`) + `IP:$RFA_SANDBOX_HOST` 서버 인증서. CA 는 onboard 때 `NEMOCLAW_CORPORATE_CA_BUNDLE` 로 샌드박스 신뢰 목록에 들어간다 (group/world 쓰기 권한이 있으면 nemoclaw 가 거부 → 644).
- `scripts/setup_sandbox.sh [create policy install mcp agent cron check]`
  - create: onboard (`agents/agents.yaml`, `workflow/`·`common/` 읽기 전용 host-mount. 레포 루트는 `.env` 때문에 마운트하지 않음)
  - policy: `pypi` + `policies/rfa-host.yaml`(`--trusted-private-host`). 워크플로(python)가 쓰는 경로만. approve/reject·결재 웹 없음
  - install: `/sandbox/rfa-venv` 에 rfa-common, rfa-workflow. 설정은 `/sandbox/rfa-workflow.env`(600)
  - mcp: 관리형 MCP `github` (`/github/mcp`, bearer 는 OpenShell 보관), 에이전트에게 `list_mentions` 거부
  - agent: `AGENTS.md` 업로드, exec 정책 `security=allowlist` + 목록 `rfa-workflow` 하나 (목록만 넣으면 기본값 full 이라 안 막힘 — Effective Policy 로 확인)
  - cron: `openclaw cron add` (public-desk, `RFA_DESK_CRON`)
  - check: 아래 완료 조건 값 출력
- `agents/agents.yaml`, `agents/public-desk/AGENTS.md`: public-desk 는 exec(허용 목록) + bundle-mcp 만. desk-once 실행 → 결과 보고, returned 면 `get_thread` 로 힌트를 써서 `run --from-review ID --hint` 한 번.
- MCP 경로 분리: OpenShell 의 MCP 규칙(deny-tool)은 경로 단위로 모든 바이너리에 걸린다. 그래서 같은 툴을 `/github/desk/mcp` 로도 열고(서버 `PathAlias`), desk-once(python)는 그쪽 REST 규칙으로만 간다.
- `rfa-workflow --env-file F`, `run --from-review ID`, `RunResult.target`.

**완료 조건 (결과).**
- `setup_sandbox.sh check`: inference.local 200, review GET 200, approve 403, 결재 웹 403, knowledge 200, desk MCP(bearer 없음) 401, 에이전트 MCP 경로를 python 이 쓰면 403.
- `nemoclaw rfa agent --agent public-desk -m "새 멘션을 확인해"` → exec desk-once → review `reviewed`(redact) 가 결재 웹에 나타남.
- `nemoclaw rfa mcp status github --tools` 의 tool discovery 는 실패로 나온다: nemoclaw 상태 점검이 사설 IP 엔드포인트를 검사하지 못함(`no valid managed endpoint`). 런타임 호출은 된다(에이전트의 `github__get_thread` 성공).

**남은 것.** cron 등록은 샌드박스 CLI 장치의 `operator.admin` 승격 승인이 필요하다(사람이 승인, `setup_sandbox.sh cron` 이 방법을 출력). cron 트리거 E2E 와 게시는 Step 11.

---

## Step 11: 샌드박스 E2E + README

**목표.** cron 트리거로 전체 흐름 1회, README 실행법, 제출 자료 정리.

---

## Step 12: 멘션 대기열과 복구 (at-least-once 처리)

**목표.** 조회한 멘션을 하나도 잃지 않는다. 지금은 `list_mentions`가 돌려주는 순간 "본 것"으로 기록해서(at-most-once), 조회 직후 프로세스가 죽거나 한 멘션 처리 중 예외가 나면 나머지가 영영 빠진다. 이를 **"조회 → 저장 → 커서" 순서 + 미완료 문서 복구 + 결과별 재처리 정책**으로 바꾼다.

**설계 원칙.** 별도 대기열 파일을 만들지 않고 **결재 문서 = 작업 항목**으로 쓴다. 이미 멘션 하나당 문서 하나가 영속 저장되고 `source_url`로 멱등이므로, 대기 목록·중복 방지·상태 조회가 그대로 따라온다. 대기열 로직은 전부 결정적 코드(호스트 데스크)에 두고, OpenClaw public-desk(LLM)는 `desk.poll` 툴 하나만 부른다.

**범위.**
- 포함: mcp_channels(`list_mentions` 순수 조회화), review(상태 2개·전이 2개·목록 필터), workflow desk(대기열 처리·재시도·잠금·status), CLI, 계약, 문서, 전환 절차
- 제외: 재결재 경로(Step 13), Internal 채널

**만들/고칠 파일.**
```
services/mcp_channels/github.py      # MentionTracker 제거. list_mentions(since) 는 순수 조회 (본 것 기록 안 함)
services/review/store.py             # 상태 returned 추가, 전이 opened→returned, needs_human|returned→(재개 지점) reopen, failures 필드
services/review/app.py               # POST /reviews/{id}/returned, POST /reviews/{id}/reopen (loopback 전용), GET /reviews?status= 다중값
common/rfa_common/models.py          # ReviewStatus.RETURNED, Review.failures[], Review.attempts
workflow/rfa_workflow/desk.py        # 대기열 처리: fetch → enqueue → cursor → drain(미완료 전부) → 결과별 정책, 파일 잠금
workflow/rfa_workflow/cli.py         # desk-once(재정의), retry <id> [--hint], status [--all]
workflow/rfa_workflow/graph_public.py # returned/needs_human 결과를 문서 상태에도 기록 (returned 전이 호출)
data/state/desk_cursor.json          # {since} — 데스크가 소유 (MCP 서버 아님)
data/state/desk.lock                 # 실행 잠금
contracts/review.openapi.yaml        # 상태·전이·failures 추가
docs/modules/{workflow,mcp-channels,review-service}.md, docs/setup.md
services/tests/test_github.py, test_review_core.py / workflow/tests/test_desk.py, test_queue.py
```

**동작 정의.**

1. 조회와 저장 분리
   - `list_mentions(since)`는 GitHub 를 읽어 돌려주기만 한다. `mentions_seen.json` 과 `MentionTracker` 삭제.
   - 데스크: `since = cursor.since or now-24h` 로 조회 → 멘션마다 `POST /reviews`(멱등, 기존이면 200) → **전부 저장된 뒤에만** `cursor.since = 조회 시작 시각` 저장(tmp→rename). 저장 중 죽으면 커서가 안 올라가 다음 실행에서 같은 구간을 다시 조회하고, 멱등 생성이라 중복은 없다.
   - 같은 URL 을 다시 봐도 문서는 하나(기존 `test_same_mention_returns_existing_review`).

2. 미완료 작업 복구
   - 저장 후 `GET /reviews?status=opened,knowledge_ready,scanned` 로 **미완료 문서 전부**를 처리한다. 새 멘션이 없어도 돈다.
   - 중간 상태는 Step 8 의 재개 로직(`intake`가 상태로 시작 지점 결정)을 그대로 쓴다.
   - "실행 중" 표시는 두지 않는다(죽으면 지워줄 사람이 없음). 대신 실행 잠금으로 동시 실행을 막고, 잠금이 없으면 진행 중인 것이 없다고 본다.

3. 결과별 재처리 정책
   | 결과 | 문서 상태 | 자동 재실행 |
   |---|---|---|
   | reviewed | reviewed | 안 함 (사람 결재 대기) |
   | already_handled | 그대로 | 안 함 |
   | returned | **returned** (신규 상태, 워크플로가 `POST /returned` 로 전이) | 안 함. `retry <id> --hint` 로만 |
   | needs_human | needs_human | 안 함. `retry <id>` 로만 |
   | 예상 밖 예외 | 상태 그대로 + `failures[]` 에 사유·시각 추가 | 다음 실행에서 재시도. `attempts ≥ 3` 이면 needs_human 으로 전이하고 멈춤 |
   - `retry <id> [--hint]`: `POST /reviews/{id}/reopen` (loopback 전용) → 저장 데이터가 있는 마지막 재개 지점으로 되돌림(knowledge 있으면 knowledge_ready, draft·scan 있으면 scanned, 없으면 opened) → 워크플로 실행. reviewed/approved/posted/rejected 는 reopen 거부(409). 기존 `intake`의 DONE 판정은 그대로라 needs_human 문서는 reopen 없이는 다시 돌지 않는다.

4. 부분 실패 격리
   - 문서마다 `try/except Exception` 으로 감싸 실패를 `failures[]` 에 기록하고 다음 문서로 계속. 실행 끝에 결과 요약을 출력하고, 실패가 있으면 exit 1.

5. 중복 방지·사람 결재 유지
   - 문서 생성 멱등, 제출 409 해소, approve loopback 전용은 그대로.
   - 워크플로 완료 후 결과 기록 전에 죽어도: 문서 상태는 서버에 이미 반영돼 있어 다음 실행이 `already_handled`/미완료 복구로 정확히 이어간다. 게시는 사람이 approve 를 눌러야만 일어나므로 중복 게시 없음.
   - 실행 잠금: `data/state/desk.lock` 을 `fcntl.flock` 비차단으로 잡고, 실패하면 "다른 데스크가 실행 중" 으로 즉시 종료(exit 2). 죽은 프로세스의 잠금은 OS 가 풀어준다.

6. 상태 확인
   - `rfa-workflow status`: 미완료·returned·needs_human 문서를 표로(id, 상태, 멘션 URL, attempts, 마지막 실패 사유, 갱신 시각). `--all` 이면 전부.
   - 결재 웹은 `returned` 배지("보완 필요")와 `failures` 표시를 추가.

7. 전환 (`mentions_seen.json` → 커서)
   - 기존 `seen` 은 "처리 완료"가 아니라 "돌려준 적 있음"이다. 전환 시 `desk_cursor.json` 이 없으면 `RFA_DESK_BACKFILL_HOURS`(기본 24) 전부터 조회한다. 멱등 생성 덕에 이미 문서가 있는 멘션은 그 문서로 이어지고, 없던 멘션(예전 누락분)은 이때 문서가 생긴다. 24시간보다 오래된 누락은 복구하지 못한다 — 이 한계를 `docs/modules/mcp-channels.md` 에 적는다.

**테스트 (필수 시나리오 → 테스트).**
- 조회·저장 직후 종료(커서 저장 전에 예외 주입) → 다음 실행이 같은 멘션을 다시 조회해 처리, 문서는 1개
- 멘션 3개 중 2번째가 예외 → 1·3 은 처리, 2 는 failures 1건·다음 실행에서 재시도, 3회 후 needs_human
- 워크플로 완료 후(reviewed) 결과 기록 전 종료 → 다음 실행 already_handled, 결재 문서·게시 없음
- 같은 멘션 반복 조회(커서 미갱신) → 문서 1개, 워크플로는 미완료일 때만
- needs_human / returned 는 두 번째 실행에서 건너뜀 → `retry <id> --hint` 후 재개 지점부터 처리 → reviewed
- 새 멘션 없음 + knowledge_ready 문서 존재 → 복구되어 reviewed
- reopen 이 reviewed/posted/rejected 를 거부(409), 비-loopback 403
- 두 번째 데스크 동시 실행 → exit 2, 문서·커서 변화 없음
- 커서 없음 → backfill 시각으로 조회

**완료 조건.**
```bash
uv run pytest
./scripts/run_services.sh &
# 멘션 두 개 남긴 뒤, 첫 실행을 강제로 중단(RFA_DESK_CRASH_AFTER=enqueue) → 재실행 → 둘 다 처리
RFA_LLM_MODE=mock ./scripts/demo_host.sh; uv run rfa-workflow status
uv run rfa-workflow retry 3 --hint "ORBIT 벤치마크 진행 상황"
```

---

## Step 13+ (본선 전)

Internal 대응 데스크와 채널(로컬 파일 흉내), censor_internal, `data/policy/internal/*`, personal 기준 추가 API + 화면, 결재 웹 편집 후 승인.

**재결재 경로** (설계 확정, 구현 대기 — `docs/modules/review-service.md` 상태기계 참고). Step 12 의 `reopen` 전이와 같은 결에서 설계한다:
- `rejected → drafted` 재작성 루프: 거절 사유를 writer 입력으로 되돌림. store `ALLOWED[DRAFTED]`에 `REJECTED` 추가 + revision 카운터.
- `POST /reviews/{id}/republish`: 게시 실패로 `approved`에 갇힌 문서 재게시 (loopback 전용). `ALLOWED[POSTED]`는 그대로(`APPROVED`)이므로 엔드포인트만 추가.
- 결재 웹(Step 6)은 rejected/approved 문서에 각각 "재작성 요청됨" 표시와 "재게시" 버튼 자리를 미리 잡아 둔다.
