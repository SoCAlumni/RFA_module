# 개발 계획 v2 (2026-09-27 재설계)

> 다른 스레드에서 이어서 작업할 때는 이 문서의 **현재 상태** 절부터 읽는다. 단계가 끝날 때마다 갱신한다.
> 배경·결정은 `docs/plan.md`, 팀 경계 API 는 `docs/contracts.md`, 리셋 내역은 `docs/migration.md`.

## 현재 상태

| 항목 | 값 |
|---|---|
| 진행 중 단계 | **Step 5** (`step-05-workflow`) — PR 리뷰 대기 |
| 마지막 머지 | Step 3 (PR #17) |
| 다음 할 일 | Step 5 PR 리뷰·머지 → Step 4 (채널 인터페이스 + GitHub + 실제 게시) |
| 순서 변경 (9/27) | Slack 앱 세팅이 오래 걸려 Slack 을 마지막(Step 7)으로 미룸. 진행 순서: 5 → 4 → 6(GitHub E2E) → 7(Slack) |
| 사람이 할 일 | `person/TODO.md` (Slack 앱 만들기, 팀 확인 사항) |

## 규칙

- **한 단계 = 재현 가능한 기능 하나 = PR 하나.** PR 은 사용자가 리뷰하고 머지한다. 머지 전에는 다음 단계를 시작하지 않는다.
- 브랜치 `step-NN-<name>`, PR 제목 `Step NN: <기능>`. PR 본문에 재현 명령, 테스트 결과, 셀프리뷰 결과를 붙인다.
- 단계마다 테스트를 쓰고 `uv run pytest` 와 `uv run ruff check . && uv run ruff format --check .` 가 통과해야 한다.
- 셀프 코드리뷰 체크리스트 (PR 전에 확인, PR 본문에 결과 기재):
  - [ ] dead code 없음: 미사용 import, 함수, 파일, 설정 키, 주석 처리된 코드
  - [ ] 모든 공개 함수/엔드포인트에 동작을 증명하는 테스트가 있음
  - [ ] 실패 경로(잘못된 입력, 순서 위반, 외부 오류)도 테스트됨
  - [ ] 문서(`docs/*.md`, `contracts/*.yaml`)와 코드가 일치함
  - [ ] 비밀값이 코드/커밋에 없음
  - [ ] 이 단계 범위 밖 코드를 미리 넣지 않음
- 공통 도구: Python 3.12+, `uv`, `pytest`, `ruff`. FastAPI, pydantic v2, httpx, LangGraph, slack_sdk.

## 진행 표

| Step | 기능 | 브랜치 | 상태 |
|---|---|---|---|
| 1 | API 계약 확정 + 팀 공유 (`/ask`, `/approvals`) | `step-01-contracts` | 완료 (PR #15) |
| 2 | 리셋 (v1 코드 정리) + 문서 + head_stub | `step-02-reset` | 완료 (PR #16) |
| 3 | approvals 서비스 + 참조 결재 웹 | `step-03-approvals` | 완료 (PR #17) |
| 5 | workflow — LangGraph 그래프 (Slack 없이 가능해 4 보다 먼저) | `step-05-workflow` | 리뷰 대기 |
| 4 | channels — 공통 인터페이스 + GitHub + 실제 게시(live) | `step-04-channels` | |
| 6 | desk 상주 루프 + 스크립트 + GitHub E2E | `step-06-desk` | |
| 7 | Slack 어댑터 (Socket Mode) + Slack E2E | `step-07-slack` | Slack 토큰 필요 |

---

## 큰 그림

### 터미널 3개

```
   GitHub / Slack
        ▲ ▼ (읽기)
  ┌──────────────┐   "지식 줘"     ┌──────────────┐
  │ C. desk      │ ─────────────► │ B. 지식 서버  │  ← 민섭님 것으로 교체
  │ (감시+작성)   │                └──────────────┘
  │              │   "결재 올려줘"  ┌──────────────┐        ┌────────────┐
  │              │ ─────────────► │ A. 결재 서버  │ ◄───── │ 다영님 프런트 │
  │              │ ◄───────────── │  (웹 백엔드)  │        │  (브라우저)  │
  └──────────────┘  "거절된 거 있어?"└──────┬───────┘        └────────────┘
                                          │ 승인되면 게시
                                          ▼
                                    GitHub / Slack
```

| | 실행 명령 | 포트 | 역할 | 비고 |
|---|---|---|---|---|
| A. 결재 서버 (`approvals`) | `uvicorn --factory approvals.app:create_app --port 8790` | 8790 | 안건 저장·목록·승인/거절, 승인 시 게시. 브라우저 `:8790/` 참조 웹 | **웹 백엔드 = 이것.** 다영님 프런트가 부름 |
| B. 지식 서버 (`head_stub`) | `uvicorn head_stub.app --port 8791` | 8791 | `POST /ask` 하나. `data/knowledge/` 검색해 답 | 민섭님 head agent 오면 안 켬 (`HEAD_URL` 변경) |
| C. desk | `python -m rfa_workflow desk` | 없음 | 5초마다 GitHub/Slack 확인 → `graph.py`(LangGraph) 실행 → B 에 지식 요청, A 에 안건 제출. A 의 거절 안건 폴링해 재작성 | 요청을 받지 않고 보내기만 함. LangGraph 는 이 안에서 함수로 실행 |

`scripts/run_services.sh` = A+B, `scripts/run_desk.sh` = C.

### 흐름

```
1  GitHub 멘션 / Slack 멘션·DM ──► 2  desk (채널 어댑터) ──► LangGraph run(mention)
                                        │
                        3  POST {HEAD_URL}/ask  {question, channel, audience, target, url, requester, context, feedback[{draft, reason}]}
                        4  ◄── {knowledge, task, refusal}                         ← 민섭님. 지금은 head_stub
                        5  writer LLM → 초안 (채널 말투, 거절 사유 반영)
                        6  POST {APPROVALS_URL}/approvals  → pending  (그래프 종료)
                                        │
              6-1 POST /approvals/{id}/reject {reason} → rejected      6-2 POST /approvals/{id}/approve
                   desk 가 rejected 를 폴링 → run(mention, rejections)         → 백엔드가 채널에 바로 게시 → posted
                   → POST /approvals/{id}/revise → pending (round+1)
                   3회 거절이면 백엔드가 closed 로 닫음
```

### 목표 레이아웃

```
common/rfa_common/contracts.py     계약 모델 (contracts/*.openapi.yaml 과 1:1). v1 의 models.py 는 Step 2 에서 삭제
contracts/head.openapi.yaml        POST /ask (민섭님 공유용)
contracts/approvals.openapi.yaml   결재 API (다영님 프런트 공유용)
services/
  head_stub/      knowledge_stub 개명. POST /ask. loader.py·rank.py 재사용
  channels/       mcp_channels 개명. base.py(Protocol) · github.py(이식) · slack.py(새) · registry.py
  approvals/      app.py · store.py(review/store.py 축소) · publisher.py · static/index.html(기존 축소)
  tests/
workflow/rfa_workflow/
  graph.py        ask_head → write → submit (고정 그래프), run / redo
  desk.py         상주 루프 (채널 poll + rejected 폴링)          ← Step 6
  clients.py      HeadClient · ApprovalsClient
  deps.py         head · approvals · llm 묶음 (env 에서)
  llm.py          AnthropicLLM(fallbacks) · RuleLLM(mock)
  prompts/        writer.md · style_github.md · style_slack.md
  cli.py          run / redo (Step 6 에서 desk)
```

### 결재 상태기계

| to | from |
|---|---|
| approved | pending |
| posted | approved |
| rejected | pending |
| pending (revise) | rejected |
| closed | rejected (round ≥ 3 이면 reject 가 자동으로 closed 까지) |

`/approve` 는 approved → publisher.post → posted 를 한 요청에서. 게시 실패는 502 + approved 유지 (다시 누르면 게시만 재시도).

---

## Step 1: API 계약 확정 + 팀 공유  (`step-01-contracts`)

**목표.** 두 팀원과 맞닿는 경계를 먼저 못 박고 공유한다. v1 코드는 건드리지 않는다 (삭제는 Step 2).

**만들 파일.**
```
common/rfa_common/contracts.py      새 모델 (v1 models.py 와 별도 파일 — 이름 충돌 없이 공존)
contracts/head.openapi.yaml
contracts/approvals.openapi.yaml    (v1 의 review/knowledge yaml 은 Step 2 에서 삭제)
docs/contracts.md                   팀 공유 한 장
docs/develop_plan.md                이 문서
services/tests/test_contracts.py    모델 검증 + yaml properties ↔ 모델 필드 대조
```

**동작 정의.** `docs/contracts.md` 참고.

**끝나면.** PR → 머지 → `.github/workflows/api-docs.yml` 이 Redoc 을 https://socalumni.github.io/RFA_module/ 에 배포 → 사용자가 `docs/contracts.md` 와 Pages 링크를 민섭님·다영님께 전달.

## Step 2: 리셋 + 문서 + head_stub  (`step-02-reset`)

**목표.** v1 에서 소관이 넘어갔거나 방향이 다른 코드를 걷어내고, 남길 것을 새 자리로 옮긴다. 상세 내역은 `docs/migration.md`.

**사용자가 먼저 실행한 것** (권한 분류기가 Claude 의 대량 `git rm` 을 막음): `services/review`, MCP 서버, v1 계약·모델·정책·그래프·테스트 삭제, `knowledge_stub → head_stub`, `mcp_channels → channels` 이동.

**Claude 가 한 것.**
- `workflow/` 의 나머지 v1 파일(`cli`, `clients`, `deps`, `state`, `llm`, `__main__`, `prompts/`, `tests/wf_support.py`) 삭제 — 전부 지운 모듈을 import 해 깨진 상태. Step 5 에서 `git show 819053f:<경로>` 로 필요한 부분을 가져와 새로 쓴다. 패키지는 `__init__.py` 만 남김.
- `services/head_stub/app.py`: `POST /ask`. 모든 task 의 문서를 질문과 키워드로 대조 → 1위 문서의 task 선택 → 그 task 상위 3문서의 문장을 knowledge 로. `feedback` 의 **모든** 사유 키워드가 든 문장은 뺀다 (라운드마다 누적). 맞는 문서가 없으면 `refusal="관련 업무를 찾지 못했습니다"`, 다 빠지면 task 는 두고 `refusal="거절 사유를 반영하면 답할 수 있는 내용이 없습니다"`. 검열은 하지 않는다.
- `services/head_stub/loader.py`: `TaskInfo` 를 로컬 모델로, `Doc.task_id` 추가. 아무도 안 읽던 `summary`·`tags`·`source_line` 제거.
- `services/channels/github.py`: 새 `Mention`(`channel=github`), `parse_target` 을 로컬로. 기능 변경 없음.
- `services/pyproject.toml` packages → `head_stub, channels`. `mcp` 의존성 제거(서비스·워크플로 둘 다), 워크플로 CLI 진입점 제거. 루트 `testpaths` 에서 `workflow/tests` 제거 (Step 5 에서 다시 추가).
- `.env.example`: 지금 코드가 읽는 키만 (`RFA_DATA_DIR`, `GITHUB_*`). 나머지는 그 키를 쓰는 단계에서 추가.
- `scripts/run_services.sh`: head_stub(8791) 만 띄우도록 축소.
- 문서: `docs/migration.md`(새), `docs/plan.md`·`docs/architecture.md`·`docs/setup.md`·`README.md` 재작성.

**테스트.** `test_head_stub.py` (task 선택, 다른 task, 매치 없음, 거절 사유 반영, 사유 누적, 전부 빠짐, 채널/target 불일치 422, 문장 분리, loader, rank), `test_github.py` (v1 에서 MCP·서명 게시자 부분을 빼고 옮김 + `create_comment`·`from_env` 추가), `test_contracts.py` 유지.

## Step 3: approvals 서비스 + 참조 결재 웹  (`step-03-approvals`)

- `store.py`: v1 `review/store.py` 의 골격(안건 하나 = `<RFA_DATA_DIR>/state/approval-<id>.json`, tmp+rename 원자적 쓰기, `Step`/`ALLOWED` 전이표, `source_url` 멱등 create) 을 위 상태기계로 축소. `list(status, channel, task)` 는 updated_at 최신순, `summary()` 는 task 없는 안건을 `(none)` 으로.
- `app.py`: FastAPI + `CORSMiddleware`(`RFA_CORS_ORIGINS`, 기본 `*`). 행위자는 고정 — create/revise 는 `desk`, approve/reject 는 `human`, 게시는 `publisher`, 자동 닫힘은 `system`. 승인은 approved → 게시 → posted 를 한 요청에서 하므로 **승인 전용 lock** 으로 직렬화 (게시 도중 두 번째 클릭이 '재시도'로 착각해 두 번 게시하는 것을 막음). 오류 본문은 `{error, detail}`.
- `publisher.py`: `Publisher.publish(channel, target, body) -> url` Protocol + `MockPublisher`(기록만, `fail` 로 실패 흉내). `make_publisher(env)` 는 `RFA_PUBLISHER=mock` 만. live 는 Step 4.
- `static/index.html`: v1 결재 웹을 v2 모델로 — 사이드바(전체·결재 필요·채널별·업무별 건수, 클릭하면 필터) · 목록(status 배지, NEW) · 상세(질문·스레드·head 지식/refusal·게시될 답·거절 이력·기록) · 승인 / 거절+사유 / 게시 재시도. 외부 텍스트는 모두 `textContent`.
- `scripts/run_services.sh` 에 approvals(8790) 추가. `.env.example` 에 `RFA_PUBLISHER`, `RFA_CORS_ORIGINS`.
- 테스트 (`test_approvals.py` 25개): 생성·멱등·target 검증·404·재시작 후 id 유지, 승인·게시 내용·두 번 승인 409·**게시 도중 두 번째 클릭**(lock 을 빼면 실패함을 확인)·게시 실패 502 후 재시도·거절된 안건 승인 409, 거절 기록·빈 사유 422·pending 아닌 거절 409·revise round+1·pending 에서 revise 409·3번째 거절 closed, 목록 정렬·필터·summary, CORS(기본·지정 origin), 참조 웹, 게시자 선택, **앱 경로·메서드·태그 ↔ 계약 yaml 대조**.

## Step 5: workflow — LangGraph 그래프  (`step-05-workflow`)  ← Step 4 보다 먼저

**목표.** 멘션 하나를 결재 대기까지 데려가는 대응 에이전트. 거절된 안건은 사유를 반영해 다시 쓴다. 채널·Slack 없이 head_stub 과 결재 서버만으로 동작.

```
ask_head → write → submit → END        (어느 노드든 ServiceError/LLMError → END, failed)
```

- `graph.py`: 노드 셋과 `run(mention, deps, rejections, approval_id) -> RunResult`, `redo(approval, deps)`, `mention_of(approval)`. 상태(`State`)와 결과(`RunResult`)도 여기 (별도 state.py 없음).
  - `ask_head`: `AskRequest`(질문, 채널, 독자, 자리, 주소, 질문자, 스레드 맥락, 거절 이력) → head.
  - `write`: system = `prompts/writer.md` + `prompts/style_<channel>.md`. user = `[채널] [질문한 사람] [질문] [스레드 맥락] [답할 수 없음] [head agent 가 준 지식] [거절 이력]` 구획 (외부 글과 사내 지식을 섞지 않게).
  - `submit`: `approval_id` 가 있으면 revise, 없으면 create (같은 멘션이면 서버가 기존 안건을 돌려줌).
  - `redo` 는 안건이 rejected 가 아니면 head·LLM 을 부르기 전에 멈춘다 (헛비용 방지). 그 사이 누가 먼저 다시 썼다면 서버가 409 로 막는다.
  - 계획에 있던 `intake`(같은 멘션 건너뛰기)는 뺐다. 결재 API 에 source_url 조회가 없고, 중복 멘션은 채널 쪽(GitHub 추적 파일, Slack event_id)에서 거른다. 같은 멘션이 와도 안건은 하나로 유지된다 (테스트로 확인).
- `llm.py`: v1(`819053f`) `AnthropicLLM` 을 `text` 하나로 줄임. `client.beta.messages.create(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default")` — 모델이 안전 분류기로 거절하면 서버가 권장 모델로 다시 돌림. refusal·max_tokens·빈 응답·API 오류는 `LLMError`. 응답의 thinking·fallback 블록은 버리고 text 만. `RuleLLM`(mock) 은 head 지식을 그대로 옮김 (거절 사유는 head 가 이미 반영). 기본 모델은 `claude-sonnet-4-6` (9/27 사용자 결정, `RFA_MODEL` 로 바꿀 수 있음). 실제 API 로 sonnet-4-6·opus-5 둘 다 호출해 fallback 파라미터가 받아들여지는 것 확인.
- `clients.py`: v1 재시도 루프(연결 오류·429·5xx 를 0.5/1/2초 backoff 로 3회) 유지, 복구 예산과 409 해석은 제거. `HeadClient.ask`, `ApprovalsClient.create/revise/get`.
- `deps.py`: `HEAD_URL`, `APPROVALS_URL`, LLM 설정을 env 에서.
- `cli.py`: `python -m rfa_workflow run --mention-json … | --mention-file …`, `python -m rfa_workflow redo <id>`. 결과 JSON 한 줄, failed 면 종료 코드 1.
- 테스트 (`workflow/tests/` 27개): 그래프 13 (GitHub·Slack 정상, head 로 가는 값, writer 가 받는 프롬프트, 거절→redo 에서 feedback·round 2·새 안건 없음, head refusal, 같은 멘션 두 번, head 일시 오류 재시도, head 다운, 4xx 재시도 안 함, LLM 오류, pending 안건 redo 는 head·LLM 안 부름, 옛 스냅샷 redo 409, mention_of, 외부 글이 [질문] 구획에만), LLM 9 (요청 형태·fallback, stop_reason 3종, API 오류 2종, RuleLLM, make_llm, 프롬프트 파일), CLI·Deps 5 (run·파일 입력·redo·없는 안건·env 읽기).

## Step 4: channels — 공통 인터페이스 + GitHub + 실제 게시  (`step-04-channels`)

- `base.py`: `Channel` Protocol — `kind`, `poll() -> list[Mention]`, `post(target, body) -> str`.
- `github.py`: v1 `GithubClient`/`find_mentions`/`MentionTracker`/`BOT_MARKER` 유지. 스레드 최근 10개를 `Mention.context` 로. `create_comment` → `post`.
- `registry.py`: `make_channels(env)` — `RFA_CHANNELS=github` (Step 7 에서 `slack` 추가).
- `approvals/publisher.py` 에 `LivePublisher(channels)`, `RFA_PUBLISHER=live`.
- 테스트: `test_github.py`(`fake_github.py` 재사용), registry, LivePublisher.

## Step 6: desk 상주 루프 + 스크립트 + GitHub E2E  (`step-06-desk`)

- `desk.py`: `Desk.tick()` — 채널 `poll()` → `graph.run(mention)`; 결재 서버의 rejected 안건 → `graph.redo(approval)`. `run_forever(interval=5)`. (`ApprovalsClient.list(status)` 는 이때 추가)
- 실패한 redo 가 매 틱 LLM 을 다시 부르지 않게 안건별 재시도 간격/횟수 제한.
- `cli.py desk`, `scripts/run_services.sh`(8790, 8791), `scripts/run_desk.sh`, `README.md` 실행법.
- 테스트: 가짜 채널 + TestClient 로 tick 두 번(멘션 → pending, reject → round 2), 채널 오류 격리.
- E2E: mock(키 없이) → 실연동(GitHub 토큰 + `RFA_LLM_MODE=anthropic`).

## Step 7: Slack 어댑터 + Slack E2E  (`step-07-slack`)

- `slack.py`: `slack_sdk` (`uv add --package rfa-services slack-sdk`). `SlackChannel(bot_token, app_token)`: `start()` 가 `SocketModeClient` 리스너로 `app_mention`/`message`(im) 을 즉시 ack 하고 큐에 넣음 (봇 자신 무시, `<@BOT>` 제거, `event_id` 중복 제거). `poll()` 은 큐를 비워 Mention 으로 (context 는 `conversations.replies` 최근 10개). `post()` 는 `chat_postMessage(thread_ts=...)`, URL `https://slack.com/archives/{C}/p{ts}`.
- `registry.py` 에 `slack` 추가, `.env.example` 에 `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`.
- 테스트: `test_slack.py`(가짜 WebClient, 가짜 이벤트 payload).
- E2E: `person/TODO.md` 의 Slack 앱으로 `@rfa-desk` 멘션 → 결재 웹 승인 → 스레드 답글.

## 검증 (전체)

1. 단계마다 `uv run ruff check . && uv run ruff format --check . && uv run pytest`.
2. mock E2E: `scripts/run_services.sh` → `uv run python -m rfa_workflow run --mention-json '{...}'` → `curl :8790/approvals` 에 pending → 웹에서 거절(사유 "릴리즈 날짜") → desk 한 틱 → round 2 초안에 날짜 없음 → 승인 → mock 게시 기록.
3. 실 E2E: `.env` 에 Slack 토큰 + `RFA_CHANNELS=github,slack RFA_PUBLISHER=live RFA_LLM_MODE=anthropic` → `scripts/run_desk.sh` → Slack `#rfa-test` 에서 `@rfa-desk ORBIT 벤치마크 어때?` → 결재 웹 → 승인 → 스레드 답글.
