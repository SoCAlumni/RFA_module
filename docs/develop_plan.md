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
| 2 | knowledge stub + 데모 데이터 | |
| 3 | review 코어 (상태기계, reviews API) | |
| 4 | scanner + policy | |
| 5 | clearance + 결재 (approve/reject) | |
| 6 | 알람/결재 웹 | |
| 7 | github MCP | |
| 8 | rfa_workflow (LangGraph) | |
| 9 | 호스트 E2E | |
| 10 | 샌드박스 재현 | |
| 11 | 샌드박스 E2E + README | |

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
data/knowledge/triv3/_task.yaml, progress-2026-09.md
data/knowledge/quantization/_task.yaml, qat-notes.md
data/knowledge/quest/_task.yaml, design.md
```

**동작 정의.**
- `GET /tasks` → `TaskInfo[]` (폴더 스캔, `_task.yaml` 없는 폴더는 무시)
- `POST /tasks/{id}/ask {question}` → `KnowledgeResult`. 없는 task는 404. 상위 k=3 문서 본문을 이어 붙여 answer, 겹침 비율을 confidence, 선택 문서를 `"제목: 요약"` 문자열로 sources.
- 데모 데이터에 일부러 넣는 기밀: triv3에 미공개 모델명 `Gauss4`, 수치 `JGA 0.5%p`, 릴리즈 `11/3`, GPU pool `10.12.3.4`, 토큰 `hf_…`; quest에 경로 `/nfs/quest/`. quantization은 깨끗(대조군).
- 데이터 경로는 env `RFA_DATA_DIR`(기본 `./data`).

**테스트.** `/tasks` 3건, `ask`가 triv3 질문에 progress 문서를 sources로 반환, 없는 task 404, frontmatter 누락 md는 건너뜀.

**완료 조건.**
```bash
uv run uvicorn knowledge_stub.app:app --port 8791 &
curl -s localhost:8791/tasks | jq
curl -s -X POST localhost:8791/tasks/triv3/ask -H 'content-type: application/json' -d '{"question":"TRIV3 벤치마크 진행 어때?"}' | jq
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
services/review/store.py       # data/state/review-<id>.json, next_id, load/save, transition(id, from, to, who, what)
services/review/errors.py      # InvalidTransition → 409 {error, from, to}
services/tests/test_review_core.py
```

**동작 정의.**
| 엔드포인트 | 전이 | 본문 |
|---|---|---|
| `POST /reviews` | → opened | channel, target, source_url, requester, question |
| `GET /reviews?status=` | — | 목록(요약 필드) |
| `GET /reviews/{id}` | — | Review 전체, 없으면 404 |
| `POST /reviews/{id}/knowledge` | opened → knowledge_ready | KnowledgeResult |
| `POST /reviews/{id}/draft` | knowledge_ready → drafted | text, edit_log[] (Step 4에서 scanned까지 자동 전이 추가) |
| `POST /reviews/{id}/verdict` | drafted → reviewed (Step 4 이후 scanned → reviewed) | Verdict |
| `POST /reviews/{id}/needs-human` | 어느 상태(approved/posted 제외) → needs_human | reason |
- 모든 전이는 `events[]`에 `{at, who, what, detail}` 추가. `who`는 요청 헤더 `X-RFA-Actor`(기본 "unknown").
- 저장은 파일 단위 원자적 쓰기(tmp → rename).

**테스트.** 정상 경로 opened→…→reviewed, 각 잘못된 전이 409, 404, events 길이, 목록 status 필터, 재시작 후 로드.

**완료 조건.** `uv run pytest`, `uvicorn review.app:app --port 8790` 후 curl로 opened→reviewed 재현.

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
data/policy/feedback.jsonl   (빈 파일 또는 예시 1건)
services/tests/test_scanner.py, test_policy.py
```

**동작 정의.**
- `scan(text) -> ScanHit[]`: token(hf_/ghp_/github_pat_/sk-/AKIA/xox[bp]-), private_ip(RFC1918), internal_host(목록), internal_path(목록 접두사). 겹치는 hit는 긴 것 우선.
- `/draft`: drafted 기록 → scan → scanned 기록(events 2건).
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
- MCP(streamable-http, `/github/mcp`, bearer `GITHUB_MCP_TOKEN`): `list_mentions(since?) -> Mention[]`(notifications reason=mention, 본 것은 `data/state/mentions_seen.json`), `get_thread(target) -> Thread`.
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
workflow/prompts/{pick_task,writer,editor,style_public,censor_public}.md
workflow/tests/test_graph.py, fixtures/
```

**동작 정의.** `docs/modules/workflow.md`의 노드 표. `RFA_LLM_MODE=mock|anthropic`, `ANTHROPIC_BASE_URL`(샌드박스는 inference.local), `REVIEW_URL`, `KNOWLEDGE_URL`. 409 수신 시 needs_human.

**테스트.** pass 경로 최종 status=reviewed, revise 2회 상한, task 없음 → needs_human, review 409 → needs_human, verdict JSON 파싱 실패 1회 재시도.

---

## Step 9: 호스트 E2E

**목표.** 샌드박스 없이 전체 흐름. `scripts/run_services.sh`, `scripts/demo_host.sh`(멘션 JSON → `python -m rfa_workflow run`). 실제 LLM은 호스트 `ANTHROPIC_API_KEY`.

**완료 조건.** 테스트 이슈 멘션 → 알람 → 승인 → 수정본 게시. PR에 로그와 스크린샷.

---

## Step 10: 샌드박스 재현

**목표.** 레포 파일만으로 `rfa` 샌드박스 생성. `docs/setup.md` 순서. 산출: `scripts/setup_sandbox.sh`, `agents/agents.yaml`, `agents/public-desk/AGENTS.md`, `policies/rfa.yaml`, mkcert 안내, workflow.run 등록, cron.

**완료 조건.** `nemoclaw rfa mcp status --tools`, `agents list`, 안에서 `inference.local` 200, approve 접근 실패, `nemoclaw rfa agent --agent public-desk -m "..."` 수동 트리거로 review 생성.

---

## Step 11: 샌드박스 E2E + README

**목표.** cron 트리거로 전체 흐름 1회, README 실행법, 제출 자료 정리.

---

## Step 12+ (9/27~)

Internal 대응 데스크와 채널(로컬 파일 흉내), censor_internal, `data/policy/internal/*`, personal 기준 추가 API + 화면, 결재 웹 편집 후 승인.
