# RFA 팀 경계 API (2026-09-27)

RFA_module(승희)이 다른 두 모듈과 만나는 곳은 둘뿐입니다. 이 문서는 그 두 API 를 한 장에 정리한 것입니다.
원본은 `contracts/head.openapi.yaml`, `contracts/approvals.openapi.yaml` (Redoc: https://socalumni.github.io/RFA_module/).
파이썬 모델은 `common/rfa_common/contracts.py`.

## 누가 무엇을 부르나

| API | 부르는 쪽 | 받는 쪽 | 담당 |
|---|---|---|---|
| `POST /ask` | 대응 에이전트 (desk) | **head agent** | 민섭님이 구현. 그때까지 `services/head_stub` 이 같은 계약으로 대신함 |
| `GET /approvals`, `GET /approvals/summary`, `GET /approvals/{id}`, `POST …/approve`, `POST …/reject` | **웹 프런트엔드** | approvals 서버 (`127.0.0.1:8790`) | 다영님이 부름. 서버는 승희 |
| `POST /approvals`, `POST …/revise` | 대응 에이전트 (desk) | approvals 서버 | 내부용. 프런트는 쓰지 않음 |

전체 흐름:

```
GitHub/Slack 멘션 → desk → POST /ask → head agent (업무 선택·검열)
                       ↓ knowledge
                    초안 작성 → POST /approvals → [사람이 프런트에서 승인/거절]
                                                     승인 → 서버가 채널에 게시
                                                     거절 → desk 가 사유를 /ask 에 실어 다시 → 새 초안 → 다시 결재
```

---

## 1. `POST /ask` — 민섭님

desk 가 채널에서 받은 질문 하나를 그대로 넘깁니다. **업무 선택, 실무 에이전트 호출, 검열은 전부 head agent 안에서** 끝나고, desk 는 "그대로 써도 되는 지식"만 받습니다.

### 요청

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

| 필드 | 뜻 |
|---|---|
| `question` | 채널에 올라온 질문 원문 |
| `channel` | `github` \| `slack` — 어느 채널에서 왔는지 |
| `audience` | `public` \| `company` — **답이 나갈 독자.** GitHub 는 public, Slack 은 company. 검열 기준을 고를 때 쓰세요 |
| `target` | 답글이 달릴 자리. github `owner/repo#N`, slack `채널id/스레드ts` |
| `url` | 질문의 주소 |
| `requester` | 질문한 사람 |
| `context` | 같은 스레드의 최근 메시지 (오래된 순). 없으면 `[]` |
| `feedback` | **거절 이력.** 사람이 초안을 거절하면 desk 가 같은 질문을 다시 보내는데, 이때 `[{ "draft": "거절된 초안", "reason": "거절 사유", "at": "…" }]` 가 실립니다. 첫 요청은 `[]` |

### 응답 `200`

```json
{
  "knowledge": "ORBIT는 INT4 양자화 후 EM이 소폭 하락했고 QAT로 보완 중이다.",
  "task":      { "id": "orbit", "name": "ORBIT 벤치마크" },
  "refusal":   null
}
```

| 필드 | 뜻 |
|---|---|
| `knowledge` | 검열이 끝난 텍스트. **그대로 외부에 나가도 되는 내용만.** desk 는 이 텍스트만 근거로 초안을 씁니다 |
| `task` | head 가 고른 업무 `{id, name}`. 프런트가 업무별 목록을 만들 때 씁니다. 못 골랐으면 `null` |
| `refusal` | 답할 수 없을 때 사유 (예: `"관련 업무를 찾지 못했습니다"`). 이때 `knowledge` 는 `""` |

### 규칙

- `feedback` 가 있으면 그 사유가 다시 생기지 않게 지식을 다시 내주세요. (예: "릴리즈 날짜가 들어가 있음" → 날짜 없는 지식)
- 5xx / 타임아웃이면 desk 가 3회 재시도 후 포기합니다. 4xx 는 재시도하지 않습니다.
- 같은 안건은 최대 3번 거절되면 닫히므로 `feedback` 는 최대 2개입니다.

---

## 2. approvals API — 다영님

결재 안건의 저장·조회·승인·거절. 서버 `http://127.0.0.1:8790`, 인증 없음, CORS 허용(`RFA_CORS_ORIGINS`, 기본 `*`).

### 프런트가 부르는 5개

| 메서드 | 경로 | 요청 | 응답 | 오류 |
|---|---|---|---|---|
| `GET` | `/approvals?status=pending&channel=slack&task=orbit` | 셋 다 선택. 생략하면 전부. 마지막 변경(updated_at) 최신순 — 거절·재작성된 안건이 위로 | `Approval[]` | |
| `GET` | `/approvals/summary` | | 채널별·업무별 상태 건수 (아래) | |
| `GET` | `/approvals/{id}` | | `Approval` | 404 |
| `POST` | `/approvals/{id}/approve` | 본문 없음 | `Approval` (status `posted`, `posted_url` 채워짐) | 404 / 409 pending·approved 아님 / **502** 게시 실패 (approved 에 머묾. 다시 누르면 게시만 재시도) |
| `POST` | `/approvals/{id}/reject` | `{ "reason": "릴리즈 날짜가 들어가 있음" }` | `Approval` (status `rejected`, 3회째면 `closed`) | 404 / 409 pending 아님 / 422 reason 빈 값 |

`/approvals/summary` 응답:
```json
{
  "by_channel": { "github": { "pending": 2, "posted": 5 }, "slack": { "pending": 1 } },
  "by_task":    { "orbit": { "pending": 2 }, "prism": { "pending": 1 }, "(none)": { "posted": 2 } }
}
```

### desk 만 부르는 2개 (프런트는 쓰지 않음)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| `POST` | `/approvals` | 안건 내용 | 201 `Approval` (같은 `source_url` 있으면 200 기존 것) |
| `POST` | `/approvals/{id}/revise` | `{ task, knowledge, refusal, draft }` | `Approval` (status `pending`, round+1) |

### `Approval` — 안건 하나

```json
{
  "id": 12, "status": "pending", "round": 2,
  "channel": "github", "audience": "public",
  "task": { "id": "orbit", "name": "ORBIT 벤치마크" },
  "target": "team/rfa-test#34",
  "source_url": "https://github.com/team/rfa-test/issues/34#issuecomment-1",
  "requester": "outside-dev",
  "question": "ORBIT 벤치마크 결과가 언제쯤 공개되나요?",
  "context":  [ { "author": "outside-dev", "text": "…", "at": "2026-09-27T08:40:00Z" } ],
  "knowledge": "ORBIT는 INT4 양자화 후 EM이 소폭 하락했고 QAT로 보완 중이다.",
  "refusal": null,
  "draft": "안녕하세요. ORBIT 벤치마크는 양자화 이후 정확도 보완 작업을 진행 중입니다.",
  "rejections": [ { "draft": "…round 1 초안…", "reason": "릴리즈 날짜가 들어가 있음", "at": "2026-09-27T09:10:00Z" } ],
  "posted_url": null,
  "events": [
    { "at": "2026-09-27T08:41:00Z", "who": "desk",  "what": "pending",  "detail": null },
    { "at": "2026-09-27T09:10:00Z", "who": "human", "what": "rejected", "detail": "릴리즈 날짜가 들어가 있음" },
    { "at": "2026-09-27T09:11:00Z", "who": "desk",  "what": "pending",  "detail": "round 2" }
  ],
  "created_at": "2026-09-27T08:41:00Z", "updated_at": "2026-09-27T09:11:00Z"
}
```

| 필드 | 화면에서 |
|---|---|
| `status` | 배지. `pending` 결재 필요 · `rejected` 다시 쓰는 중 · `approved` 게시 중 · `posted` 완료 · `closed` 3회 거절로 닫힘 |
| `round` | 몇 번째 초안인지 |
| `channel`, `task`, `requester` | 목록 필터와 사이드바 |
| `question`, `context` | 원본 질문과 스레드 |
| `knowledge` | head agent 가 준 근거 (초안이 이걸 벗어나면 안 됨) |
| `draft` | **결재 대상.** 승인하면 이 텍스트가 그대로 채널에 올라감 |
| `rejections` | 이전 초안과 거절 사유 이력 |
| `posted_url` | 게시된 답글 주소 (posted 이후) |
| `events` | 상태 변화 타임라인 |

### 상태

```
pending ──approve──► approved ──게시──► posted
   │
   └──reject──► rejected ──desk 가 다시 씀──► pending (round+1)
                   │
                   └── 3회째 거절 ──► closed
```

---

## 열린 질문

1. **UI 시안의 "어디까지 공개할까요?" 3단계 선택**은 이 API 에 없습니다. 지금은 승인/거절뿐입니다. 필요하면 `reject` 사유에 범위를 적는 식으로 시작하고, 정식 필드는 따로 논의.
2. `task` 는 head agent 가 돌려주는 값에 의존합니다. head 가 `null` 을 주면 프런트에서는 `(none)` 으로 묶입니다.
3. 결재 API 에 인증이 없습니다. 승인 호출을 사람만 하게 막는 것은 샌드박스 정책(다영님) 쪽입니다.

---

## 구현·사용 예시 (살아있는 것들)

- 결재 API를 실제로 쓰는 화면: `services/approvals/static/index.html` (서버 켜면 http://127.0.0.1:8790/)
- 요청 본문 예시 dict: `services/tests/test_approvals.py` 의 `GITHUB` / `SLACK`
- `/ask` 참고 구현(대역): `services/head_stub/app.py` · 호출하는 쪽: `workflow/rfa_workflow/graph.py` 의 `ask_head`
- 계약↔코드 자동 대조 테스트: `services/tests/test_contracts.py`, `services/tests/test_approvals.py::test_app_routes_match_contract`
