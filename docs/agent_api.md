# RFA API — 다른 에이전트용 사용 설명서

> 이 문서는 **다른 AI 에이전트가 읽고 바로 RFA API 를 호출할 수 있게** 쓴 것이다.
> 각 API 마다 "무엇을 하려고 부르는지 → 무엇을 보내는지 → 무엇이 돌아오는지 → 어떤 오류가 나는지"를 적었다.
> 원본 계약: `contracts/approvals.openapi.yaml`, `contracts/head.openapi.yaml` · Redoc https://socalumni.github.io/RFA_module/
> 응답 예시와 오류 본문은 로컬 서버에 실제로 보내서 확인한 것이다 (2026-09-28).

## 0. 이 시스템이 하는 일 (30초 요약)

GitHub·Slack 에서 "나"에게 온 질문을 desk(대응 에이전트)가 받아 → head agent 에게 검열된 지식을 받고 → LLM 이 답 초안을 쓴 뒤 → **결재 안건**으로 올린다.
사람(또는 권한을 받은 에이전트)이 안건을 **승인**하면 그 채널에 내 이름으로 답글이 달리고, **거절**하면 desk 가 사유를 반영해 초안을 다시 쓴다.

```
GitHub/Slack 멘션 → desk → POST /ask (head agent: 지식)
                      ↓
                   초안 작성 → POST /approvals → [결재: 당신이 쓰는 API]
                                                 approve → 채널에 게시
                                                 reject  → desk 가 다시 씀 → 새 초안(round+1)
```

**다른 에이전트가 쓰는 것은 대부분 결재 API(§2) 다.** 지식을 공급하는 쪽(head agent)을 구현한다면 §4 를 봐라.

## 1. 빠른 시작

| 항목 | 값 |
|---|---|
| Base URL | `http://127.0.0.1:8790` (결재 서버) |
| 인증 | 없음 |
| 형식 | 요청·응답 모두 JSON (`content-type: application/json`) |
| CORS | `RFA_CORS_ORIGINS` (기본 `*`) |
| 기계가 읽는 명세 | `GET http://127.0.0.1:8790/openapi.json` · 사람용 `/docs` |
| 시간 | 모두 ISO-8601 UTC (`2026-09-27T09:10:00Z`) |

서버가 떠 있는지 확인:
```bash
curl -s http://127.0.0.1:8790/approvals/summary
# {"by_channel":{"slack":{"posted":1,"pending":1}},"by_task":{"orbit":{"posted":1,"pending":1}}}
```

> **네트워크 주의.** 서버는 기본으로 `127.0.0.1` 에만 열려 있다. 에이전트가 같은 컴퓨터에서 돌면 그대로 되고,
> 다른 기기·컨테이너에서 부르려면 서버 주인이 `--host 0.0.0.0` 으로 다시 띄우고 IP 를 알려 줘야 한다.
> 인증이 없으므로 신뢰할 수 있는 네트워크에서만 연다.

## 2. 결재 API — 목적별 안내

### 무엇을 하고 싶은가 → 어떤 API

| 하고 싶은 것 | API | 상태를 바꾸나 |
|---|---|---|
| 결재할 안건이 있는지 보기 | [`GET /approvals?status=pending`](#21-get-approvals--안건-목록) | 아니오 |
| 채널별·업무별로 몇 건인지 세기 | [`GET /approvals/summary`](#22-get-approvalssummary--건수-요약) | 아니오 |
| 안건 하나를 자세히 보기 (초안·근거·거절 이력) | [`GET /approvals/{id}`](#23-get-approvalsid--안건-상세) | 아니오 |
| 초안을 승인해서 채널에 게시하기 | [`POST /approvals/{id}/approve`](#24-post-approvalsidapprove--승인하고-게시) | **예 — 외부에 글이 올라감** |
| 초안을 거절하고 고쳐 달라고 하기 | [`POST /approvals/{id}/reject`](#25-post-approvalsidreject--거절하고-다시-쓰게-하기) | 예 |

`POST /approvals`, `POST /approvals/{id}/revise` 는 desk 전용이다. **다른 에이전트는 부르지 않는다** (§3).

---

### 2.1 `GET /approvals` — 안건 목록

**목적**: 결재함을 훑는다. 보통 `status=pending` 으로 "지금 결재가 필요한 것"만 가져온다.

**요청** — 쿼리 파라미터 (모두 선택, 생략하면 전부)

| 파라미터 | 값 | 뜻 |
|---|---|---|
| `status` | `pending` \| `approved` \| `posted` \| `rejected` \| `closed` | 상태로 거르기 |
| `channel` | `github` \| `slack` | 질문이 온 채널 |
| `task` | 문자열 (예: `orbit`) | head agent 가 고른 업무 id (`Approval.task.id`) |

```bash
curl -s 'http://127.0.0.1:8790/approvals?status=pending&channel=github'
```

**응답 `200`** — [`Approval`](#26-approval--안건-객체) 배열. **`updated_at` 최신순**이라 방금 거절·재작성된 안건이 위로 온다. 없으면 `[]`.

**오류**

| 코드 | 언제 | 본문 |
|---|---|---|
| 422 | `status`·`channel` 에 허용 밖의 값 | `{"detail":[{"type":"enum","loc":["query","status"],"msg":"Input should be 'pending', ...","input":"foo",...}]}` |

---

### 2.2 `GET /approvals/summary` — 건수 요약

**목적**: 안건을 다 받지 않고 현황만 본다 (대시보드, "대기 중인 게 몇 개?" 에 답할 때).

```bash
curl -s http://127.0.0.1:8790/approvals/summary
```

**응답 `200`**
```json
{
  "by_channel": { "github": { "pending": 2, "posted": 5 }, "slack": { "pending": 1, "rejected": 1 } },
  "by_task":    { "orbit": { "pending": 2, "posted": 3 }, "prism": { "pending": 1 }, "(none)": { "posted": 2 } }
}
```
- `by_channel`: 채널 → 상태 → 건수. `by_task`: 업무 id → 상태 → 건수.
- 업무가 없는(`task: null`) 안건은 키 `"(none)"` 으로 묶인다. 0건인 상태는 키가 없다.

---

### 2.3 `GET /approvals/{id}` — 안건 상세

**목적**: 승인·거절을 판단하기 전에 초안(`draft`), 근거 지식(`knowledge`), 원래 질문(`question`, `context`), 이전 거절 이력(`rejections`)을 본다.

```bash
curl -s http://127.0.0.1:8790/approvals/12
```

**응답 `200`** — [`Approval`](#26-approval--안건-객체) 하나.

**오류**

| 코드 | 언제 | 본문 |
|---|---|---|
| 404 | 없는 id | `{"error":"not_found","detail":"approval 999 not found"}` |

---

### 2.4 `POST /approvals/{id}/approve` — 승인하고 게시

**목적**: 초안을 확정해 **원래 채널(GitHub 이슈 댓글 / Slack 스레드)에 내 이름으로 게시**한다. 게시까지 이 요청 한 번에 끝난다.

> ⚠️ **되돌릴 수 없는 외부 동작이다.** 서버가 `RFA_PUBLISHER=live` 면 실제로 글이 올라간다
> (`mock` 이면 기록만 남고 `posted_url` 이 `mock://...` 이 된다). 에이전트가 이 API 를 자율로 불러도 되는지는
> 사람이 정한다. 허락받지 않았다면 부르지 말고 사람에게 안건 id 를 알려라.

**요청** — 본문은 선택 (v0.3.0).
- 없음 / `{}` / 빈 `draft` / 저장된 초안과 같은 `draft` → 저장된 초안을 게시
- `{"draft": "고친 최종 본문"}` → `pending` 에서만. 게시 전에 초안을 바꾸고 원래 초안을 `edits` 에 남긴다
```bash
curl -s -X POST http://127.0.0.1:8790/approvals/12/approve
```

**응답 `200`** — `status: "posted"`, `posted_url` 이 채워진 `Approval`. `events` 끝에 `approved`(who `human`) → `posted`(who `publisher`) 가 붙는다.
```json
{ "id": 2, "status": "posted", "round": 2, "posted_url": "mock://slack/C0123ABC/1727000000.000200/1", "...": "..." }
```

**오류**

| 코드 | 언제 | 본문 | 대응 |
|---|---|---|---|
| 404 | 없는 id | `{"error":"not_found","detail":"approval 12 not found"}` | |
| 409 | `pending`·`approved` 가 아닌 안건 (이미 게시됨, 거절됨, 닫힘) | `{"error":"invalid_transition","detail":"cannot move from posted to approved"}` | 목록을 다시 읽어 상태를 확인 |
| 409 | `approved`(게시 재시도)에서 다른 `draft` 를 보냄 | `{"error":"edit_not_allowed",...}` | 본문 없이 재시도 |
| 502 | 채널 게시 실패. 안건은 `approved` 에 남는다 | `{"error":"publish_failed","detail":"..."}` | 같은 approve 를 다시 부르면 **게시만** 재시도 |

---

### 2.5 `POST /approvals/{id}/reject` — 거절하고 다시 쓰게 하기

**목적**: 초안이 틀렸거나 내보내면 안 되는 내용이 있을 때, **사유**를 남겨 돌려보낸다. desk 가 몇 초 안에 사유를 head agent 에 실어 지식을 다시 받고, 새 초안을 `pending`(round+1)으로 올린다.

**요청**
```json
{ "reason": "릴리즈 날짜와 GPU 주소 빼 주세요" }
```
| 필드 | 필수 | 뜻 |
|---|---|---|
| `reason` | 예, 1자 이상 | 무엇이 문제인지. **구체적으로** 쓸수록 다음 초안이 정확해진다 (예: "지연 수치는 빼 주세요") |

```bash
curl -s -X POST http://127.0.0.1:8790/approvals/12/reject \
  -H 'content-type: application/json' -d '{"reason":"릴리즈 날짜가 들어가 있음"}'
```

**응답 `200`** — `status: "rejected"` 인 `Approval`. 거절된 초안과 사유가 `rejections` 끝에 쌓인다.
**3번째 거절이면 `status: "closed"`** 로 끝나고 더는 다시 쓰지 않는다.

**오류**

| 코드 | 언제 | 본문 |
|---|---|---|
| 404 | 없는 id | `{"error":"not_found",...}` |
| 409 | `pending` 이 아닌 안건 | `{"error":"invalid_transition","detail":"cannot move from posted to rejected"}` |
| 422 | `reason` 이 비었거나 없음 | `{"detail":[{"type":"string_too_short","loc":["body","reason"],...}]}` |

**거절 뒤 새 초안 기다리기**: `GET /approvals/{id}` 를 5초 간격으로 읽어 `status` 가 다시 `pending` 이 되고 `round` 가 1 올랐는지 본다.
desk 가 3번 실패하면 포기하므로, 1분 넘게 `rejected` 에 머물면 사람에게 알린다.

---

### 2.6 `Approval` — 안건 객체

모든 결재 API 가 돌려주는 모양이다.

```json
{
  "id": 12,
  "status": "pending",
  "round": 2,
  "channel": "github",
  "audience": "public",
  "task": { "id": "orbit", "name": "ORBIT 벤치마크" },
  "target": "team/rfa-test#34",
  "source_url": "https://github.com/team/rfa-test/issues/34#issuecomment-1",
  "requester": "outside-dev",
  "question": "ORBIT 벤치마크 결과가 언제쯤 공개되나요?",
  "context": [ { "author": "outside-dev", "text": "ORBIT 벤치마크 결과가 언제쯤 공개되나요?", "at": "2026-09-27T08:40:00Z" } ],
  "knowledge": "ORBIT는 INT4 양자화 후 EM이 소폭 하락했고 QAT로 보완 중이다.",
  "refusal": null,
  "draft": "안녕하세요. ORBIT 벤치마크는 양자화 이후 정확도 보완 작업을 진행 중입니다.",
  "rejections": [ { "draft": "안녕하세요. ORBIT 는 11/3 릴리즈에 맞춰 …", "reason": "릴리즈 날짜가 들어가 있음", "at": "2026-09-27T09:10:00Z" } ],
  "posted_url": null,
  "events": [
    { "at": "2026-09-27T08:41:00Z", "who": "desk",  "what": "pending",  "detail": null },
    { "at": "2026-09-27T09:10:00Z", "who": "human", "what": "rejected", "detail": "릴리즈 날짜가 들어가 있음" },
    { "at": "2026-09-27T09:11:00Z", "who": "desk",  "what": "pending",  "detail": "round 2" }
  ],
  "created_at": "2026-09-27T08:41:00Z",
  "updated_at": "2026-09-27T09:11:00Z"
}
```

| 필드 | 타입 | 뜻 |
|---|---|---|
| `id` | int | 안건 번호 |
| `status` | enum | `pending` 결재 필요 · `approved` 게시 중(또는 게시 실패) · `posted` 완료 · `rejected` desk 가 다시 쓰는 중 · `closed` 3회 거절로 닫힘 |
| `round` | int ≥1 | 몇 번째 초안인지. 거절 후 재작성마다 +1 |
| `channel` | `github` \| `slack` | 질문이 온 곳 = 답이 나갈 곳 |
| `audience` | `public` \| `company` | 답을 읽을 독자. GitHub → public(외부인), Slack → company(사내) |
| `task` | `{id, name}` \| null | head agent 가 고른 업무 |
| `target` | string | 답글 자리. GitHub `owner/repo#N`, Slack `<채널id>/<스레드ts>` |
| `source_url` | string | 원래 멘션의 주소 (사람에게 보여 줄 링크) |
| `requester` | string | 질문한 사람 |
| `question` | string | 질문 원문 |
| `context` | `[{author,text,at}]` | 같은 스레드의 최근 메시지, 오래된 순 |
| `knowledge` | string | head agent 가 준 **검열된 근거**. 초안은 이것을 벗어나면 안 된다 |
| `refusal` | string \| null | head 가 답을 거부한 사유 (예: "관련 업무를 찾지 못했습니다") |
| `draft` | string | **결재 대상.** 승인하면 이 텍스트가 그대로 게시된다 |
| `rejections` | `[{draft,reason,at}]` | 이전 초안과 거절 사유, 오래된 순 |
| `edits` | `[{draft,at}]` | v0.3.0. 승인 때 사람이 고쳤다면 고치기 전 원래 초안 (고친 본문은 `draft`) |
| `posted_url` | string \| null | 게시된 답글 주소 (`posted` 이후) |
| `events` | `[{at,who,what,detail}]` | 상태 변화 기록. `who`: `desk` / `human` / `publisher` |
| `created_at`, `updated_at` | datetime | 생성·마지막 변경 시각 |

### 2.7 상태 전이

```
pending ──approve──► approved ──게시 성공──► posted
   │                    │
   │                    └─ 게시 실패(502): approved 에 머묾 → approve 다시 부르면 재시도
   │
   └──reject──► rejected ──desk 가 새 초안(revise)──► pending (round+1)
                   │
                   └── 3번째 reject ──► closed (끝)
```

- 승인할 수 있는 상태: `pending`, `approved`(게시 재시도). 거절할 수 있는 상태: `pending` 만.
- 이 외의 전이는 409 `invalid_transition`.

### 2.8 오류 형식 정리

| 코드 | 본문 모양 | 뜻 |
|---|---|---|
| 404 | `{"error":"not_found","detail":"..."}` | 없는 안건 |
| 409 | `{"error":"invalid_transition","detail":"cannot move from X to Y"}` | 지금 상태에서 할 수 없는 동작 |
| 422 | `{"detail":[{"type","loc","msg","input",...}]}` (FastAPI 검증 오류) | 요청 형식이 틀림 |
| 502 | `{"error":"publish_failed","detail":"..."}` | 채널 게시 실패 |

## 3. desk 전용 API (참고만, 호출 금지)

다른 에이전트가 부르면 결재함이 오염된다. 모양을 이해하는 용도로만 적는다.

| API | 목적 | 요청 | 응답 |
|---|---|---|---|
| `POST /approvals` | desk 가 새 초안을 안건으로 올림 (→ `pending`) | `{channel, audience, target, source_url, requester, question, context?, task?, knowledge, refusal?, draft}` | 201 새 `Approval` · 같은 `source_url` 이 이미 있으면 200 기존 것 · 422 형식 오류 |
| `POST /approvals/{id}/revise` | 거절된 안건에 새 초안 (`rejected` → `pending`, round+1) | `{task?, knowledge, refusal?, draft}` | 200 `Approval` · 404 · 409 `rejected` 아님 |

새 질문을 결재함에 넣고 싶다면 API 가 아니라 채널(GitHub 이슈에 `@<RFA_GITHUB_LOGIN>` 멘션)로 넣거나, 사람이 CLI 로 주입한다:
```bash
uv run --env-file .env python -m rfa_workflow run --mention-json '{"channel":"github","target":"owner/repo#1",
  "author":"someone","text":"ORBIT 벤치마크 진행 어때요?","url":"https://github.com/owner/repo/issues/1",
  "created_at":"2026-09-28T10:00:00Z"}'
```

## 4. head agent API — `POST /ask` (지식을 공급하는 쪽일 때만)

**누가 누구를 부르나**: desk 가 **부르는 쪽**, head agent 가 **받는 쪽**이다. 당신이 head agent 를 구현한다면 이 계약대로 서버를 만들고,
RFA 쪽 `.env` 의 `HEAD_URL` 을 당신 서버 주소로 바꾸면 통합이 끝난다. 현재 로컬 desk 는 rfa_mas head agent(`http://127.0.0.1:8799/v1/head`, 응답 최대 ~110초)를 쓴다. 대역 `services/head_stub`(`http://127.0.0.1:8791`)도 같은 계약으로 응답한다.

**목적**: 채널에 온 질문 하나에 대해 "그대로 외부에 나가도 되는" 검열된 지식을 받는다. 업무 선택·실무 에이전트 호출·검열은 전부 head agent 안에서 한다.

**요청**
```json
{
  "question":  "ORBIT 벤치마크 결과가 언제쯤 공개되나요? 양자화 후 정확도는요?",
  "channel":   "github",
  "audience":  "public",
  "target":    "team/rfa-test#34",
  "url":       "https://github.com/team/rfa-test/issues/34#issuecomment-1",
  "requester": "outside-dev",
  "context":   [ { "author": "outside-dev", "text": "…", "at": "2026-09-27T08:40:00Z" } ],
  "feedback":  []
}
```

| 필드 | 필수 | 뜻 |
|---|---|---|
| `question` | 예 | 질문 원문 |
| `channel` | 예 | `github` \| `slack` |
| `audience` | 예 | `public` \| `company` — 답을 읽을 독자. **검열 기준을 고를 때 쓴다** |
| `target` | 예 | 답글 자리 (`owner/repo#N` 또는 `채널id/스레드ts`) |
| `url` | 예 | 질문의 주소 |
| `requester` | 예 | 질문한 사람 |
| `context` | 아니오 | 같은 스레드 최근 메시지, 오래된 순. 기본 `[]` |
| `feedback` | 아니오 | **거절 이력** `[{draft, reason, at}]`. 첫 요청은 `[]`, 최대 2개. 있으면 그 사유가 다시 생기지 않게 지식을 다시 내야 한다 |

**응답 `200`**
```json
{ "knowledge": "ORBIT는 INT4 양자화 후 EM이 소폭 하락했고 QAT로 보완 중이다.", "task": { "id": "orbit", "name": "ORBIT 벤치마크" }, "refusal": null }
```
답할 수 없을 때:
```json
{ "knowledge": "", "task": null, "refusal": "관련 업무를 찾지 못했습니다" }
```

| 필드 | 뜻 |
|---|---|
| `knowledge` | 검열이 끝난 텍스트. desk 는 이것만 근거로 초안을 쓴다 |
| `task` | 고른 업무 `{id, name}` 또는 null. 결재함의 업무별 필터가 이 값을 쓴다 |
| `refusal` | 답을 거부한 사유. 이때 `knowledge` 는 `""` |

**오류·재시도**: 422 요청 형식 오류. desk 는 5xx·타임아웃이면 3회 재시도 후 포기하고, 4xx 는 재시도하지 않는다.

```bash
curl -s -X POST http://127.0.0.1:8791/ask -H 'content-type: application/json' -d '{
  "question":"ORBIT 벤치마크 진행 어때?","channel":"github","audience":"public",
  "target":"owner/repo#1","url":"https://github.com/owner/repo/issues/1","requester":"someone","feedback":[]}'
```

## 5. 권장 사용 흐름 (결재 에이전트)

```
loop (5~10초 간격):
  1. GET /approvals?status=pending                → 결재할 안건들
  2. 각 안건마다 GET /approvals/{id} 로 draft·knowledge·question·rejections 확인
  3. 판단:
     - draft 가 knowledge 를 벗어나거나, 기밀(날짜·주소·토큰 등)이 있거나, 질문에 답하지 않으면
         → POST /approvals/{id}/reject {"reason": "<무엇을 어떻게 고칠지 한 문장>"}
     - 문제 없으면
         → (자율 승인 권한이 있을 때만) POST /approvals/{id}/approve
         → 권한이 없으면 사람에게 "안건 #id 승인 대기, 링크 source_url" 을 알린다
  4. 409 가 오면 다른 누군가가 먼저 처리한 것 → 건너뛴다
  5. 502 가 오면 잠시 뒤 approve 를 한 번 더, 또 실패하면 사람에게 알린다
```

- `audience` 가 `public`(GitHub) 이면 외부인이 읽는다 — 더 보수적으로 판단한다.
- `rejections` 에 이미 같은 사유가 있는데 또 같은 문제가 있으면, 사유를 더 구체적으로 쓴다. 3번째 거절은 안건을 닫는다.

## 6. 에이전트 프롬프트에 붙여 넣을 요약

```
RFA 결재 API를 써라. 상세 설명서: RFA_module/docs/agent_api.md
- Base URL: http://127.0.0.1:8790  (인증 없음, JSON)
- 명세: GET /openapi.json
- 조회: GET /approvals[?status=&channel=&task=], GET /approvals/summary, GET /approvals/{id}
- 결재: POST /approvals/{id}/approve (본문 선택 {"draft": "고친 본문"}, pending 에서만. 채널에 실제 게시됨)
        POST /approvals/{id}/reject {"reason": "..."} (desk 가 사유 반영해 재작성, 3회째 closed)
- 금지: POST /approvals, POST /approvals/{id}/revise (desk 전용)
- 상태: pending→approve→posted / pending→reject→rejected→(재작성)pending(round+1) / 3회 거절 closed
- 오류: 404 not_found, 409 invalid_transition / edit_not_allowed, 422 형식 오류, 502 publish_failed(approve 재호출=게시 재시도)
- 승인(approve)은 [사람 확인 후에만 / 자율로] 호출한다.
```
