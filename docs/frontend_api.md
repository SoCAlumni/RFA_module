# 결재 API — 웹 프런트엔드용

> 결재 서버(`services/approvals`)가 **응답하는** API 중 웹 프런트가 부르는 5개만 모았다.
> 원본 계약: `contracts/approvals.openapi.yaml` · Redoc https://socalumni.github.io/RFA_module/ · 에이전트용 설명서: [agent_api.md](agent_api.md)
> 응답 예시·CORS 헤더는 로컬 서버에서 실제로 확인한 것이다 (2026-09-28). 계약 버전 **v0.3.0** (승인 때 초안 수정 추가).

## 1. 기본 정보

| 항목 | 값 |
|---|---|
| Base URL | `http://127.0.0.1:8790` (다른 기기에서 부르면 서버를 `--host 0.0.0.0` 으로 띄우고 그 IP) |
| 인증 | 없음 |
| 형식 | JSON. `POST` 본문이 있으면 `content-type: application/json` |
| CORS | `access-control-allow-origin: *` (서버 `.env` 의 `RFA_CORS_ORIGINS` 로 제한 가능). preflight 허용 헤더는 `content-type` |
| 시간 | ISO-8601 UTC, 마이크로초 포함 가능 (`2026-09-27T15:37:50.271069Z`) → `new Date(s)` 로 바로 파싱 |
| 명세 | `GET /openapi.json` · Swagger `GET /docs` |
| 참조 구현 | `services/approvals/static/index.html` (같은 API 를 쓰는 바닐라 JS 화면, 3초 폴링) |

## 2. 화면 ↔ API

| 화면 요소 | API | 언제 부르나 |
|---|---|---|
| 결재함 목록 (탭·필터) | `GET /approvals?status=&channel=&task=` | 진입 시 + 3~5초 폴링 |
| 사이드바 건수 (채널별·업무별) | `GET /approvals/summary` | 목록과 같이 폴링 |
| 안건 상세 (초안·근거·이력) | `GET /approvals/{id}` | 목록에서 선택 시 (목록 응답에도 전체 필드가 있어 생략 가능) |
| **승인** 버튼 (초안을 고쳐서 승인 포함) | `POST /approvals/{id}/approve` (선택 본문 `{draft}`) | 사람이 누를 때만 |
| **거절** 버튼 + 사유 입력 | `POST /approvals/{id}/reject` | 사람이 사유를 쓰고 누를 때 |

## 3. 타입 (TypeScript)

```ts
type ApprovalStatus = "pending" | "approved" | "posted" | "rejected" | "closed";
type Channel = "github" | "slack";
type Audience = "public" | "company";          // public = 사외(GitHub), company = 사내(Slack)

interface TaskRef { id: string; name: string }
interface ThreadMessage { author: string; text: string; at: string }
interface Rejection { draft: string; reason: string; at: string }
interface ApprovalEvent { at: string; who: "desk" | "human" | "publisher" | string; what: string; detail: string | null }

interface Approval {
  id: number;
  status: ApprovalStatus;
  round: number;                 // 몇 번째 초안 (거절 후 재작성마다 +1)
  channel: Channel;
  audience: Audience;
  task: TaskRef | null;          // head agent 가 고른 업무. null 이면 summary 에서 "(none)"
  target: string;                // github "owner/repo#N" | slack "C0123ABC/1727000000.000100"
  source_url: string;            // 원래 질문 링크
  requester: string;
  question: string;              // 질문 원문. GitHub 에서 온 건 마크다운(이미지·코드블록 포함)일 수 있다
  context: ThreadMessage[];      // 같은 스레드 최근 메시지, 오래된 순
  knowledge: string;             // head agent 가 준 근거
  refusal: string | null;        // head 가 답을 거부한 사유
  draft: string;                 // 결재 대상. 승인하면 이 텍스트가 그대로 게시된다
  rejections: Rejection[];       // 이전 초안 + 거절 사유, 오래된 순
  edits?: { draft: string; at: string }[];  // v0.3.0: 승인 때 사람이 고쳤다면 고치기 전 원래 초안 (고친 본문은 draft)
  posted_url: string | null;     // 게시 후 답글 주소. 서버가 mock 모드면 "mock://..."
  events: ApprovalEvent[];       // 상태 타임라인
  created_at: string;
  updated_at: string;
}

type Summary = {
  by_channel: Record<string, Partial<Record<ApprovalStatus, number>>>;
  by_task: Record<string, Partial<Record<ApprovalStatus, number>>>;   // task 없는 안건은 "(none)"
};

// 오류 본문은 두 가지 모양
type AppError = { error: "not_found" | "invalid_transition" | "edit_not_allowed" | "publish_failed"; detail: string };
type ValidationError = { detail: { type: string; loc: (string | number)[]; msg: string; input: unknown }[] };
```

## 4. 엔드포인트

### 4.1 `GET /approvals` — 목록

| 쿼리 | 값 | 생략 시 |
|---|---|---|
| `status` | `pending` \| `approved` \| `posted` \| `rejected` \| `closed` | 전부 |
| `channel` | `github` \| `slack` | 전부 |
| `task` | 업무 id (예: `orbit`) | 전부 |

- 응답 `200`: `Approval[]`, **`updated_at` 최신순** (거절·재작성된 안건이 위로). 없으면 `[]`.
- 오류 `422`: 허용 밖의 `status`·`channel` 값 (`ValidationError`).

```ts
const list = await api<Approval[]>(`/approvals?${new URLSearchParams({ status: "pending" })}`);
```

### 4.2 `GET /approvals/summary` — 건수

응답 `200` (실제 값):
```json
{
  "by_channel": { "github": { "pending": 12 }, "slack": { "posted": 1, "pending": 1 } },
  "by_task": { "orbit": { "pending": 7, "posted": 1 }, "quantization": { "pending": 3 }, "prism": { "pending": 3 } }
}
```
0건인 상태는 키가 없다 → `counts.pending ?? 0` 으로 읽는다.

### 4.3 `GET /approvals/{id}` — 상세

- 응답 `200`: `Approval`
- 오류 `404`: `{"error":"not_found","detail":"approval 999 not found"}`

<details><summary>실제 응답 예시 (안건 #2, 한 번 거절 후 재작성 → 승인)</summary>

```json
{
  "id": 2, "status": "posted", "round": 2,
  "channel": "slack", "audience": "company",
  "task": { "id": "orbit", "name": "ORBIT 모델 벤치마크" },
  "target": "C0123ABC/1727000000.000200",
  "source_url": "https://slack.com/archives/C0123ABC/p1727000000000200",
  "requester": "product-team",
  "question": "ORBIT 벤치마크 진행 어때요?",
  "context": [],
  "knowledge": "Nimbus2 0.6B를 INT4로 양자화해 ORBIT 벤치마크를 돌렸다. …",
  "refusal": null,
  "draft": "Nimbus2 0.6B를 INT4로 양자화해 ORBIT 벤치마크를 진행했는데, 하락 폭이 목표(0.3%p 이내)를 넘어서 현재 QAT를 적용해 보완하고 있어요. …",
  "rejections": [
    { "draft": "… 평균 지연은 38ms에서 27ms로 줄었으나 …", "reason": "지연 수치는 빼 주세요", "at": "2026-09-27T15:37:46.181393Z" }
  ],
  "posted_url": "mock://slack/C0123ABC/1727000000.000200/1",
  "events": [
    { "at": "2026-09-27T15:37:31.871821Z", "who": "desk", "what": "pending", "detail": null },
    { "at": "2026-09-27T15:37:46.182187Z", "who": "human", "what": "rejected", "detail": "지연 수치는 빼 주세요" },
    { "at": "2026-09-27T15:37:50.028409Z", "who": "desk", "what": "pending", "detail": "round 2" },
    { "at": "2026-09-27T15:37:50.269786Z", "who": "human", "what": "approved", "detail": null },
    { "at": "2026-09-27T15:37:50.271069Z", "who": "publisher", "what": "posted", "detail": "mock://slack/C0123ABC/1727000000.000200/1" }
  ],
  "created_at": "2026-09-27T15:37:31.871821Z",
  "updated_at": "2026-09-27T15:37:50.271069Z"
}
```
</details>

### 4.4 `POST /approvals/{id}/approve` — 승인하고 게시

> ⚠️ 서버가 `RFA_PUBLISHER=live` 면 **GitHub·Slack 에 실제로 글이 올라간다** (되돌릴 수 없음). 버튼에 확인 단계를 두는 것을 권장.

- 요청 (v0.3.0): 본문은 **선택**.
  - 본문 없음 / `{}` / `{"draft": ""}` / 저장된 초안과 같은 `draft` → 저장된 초안을 그대로 게시
  - `{"draft": "사람이 고친 최종 본문"}` → `pending` 에서만. 게시 전에 `draft` 를 바꾸고 원래 초안을 `edits` 에 남긴다 (이벤트 `human` · `approved` · detail `"edited"`)
- 응답 `200`: `status: "posted"`, `posted_url` 채워진 `Approval`. 게시까지 이 요청 하나로 끝난다 (응답이 몇 초 걸릴 수 있음 → 버튼 로딩 상태)
- 초안 편집 UI: 텍스트 영역의 값이 `draft` 와 다를 때만 본문에 실어 보내고, "수정함 · N자" 처럼 표시하면 된다.

| 오류 | 본문 `error` | 뜻 | UI 처리 |
|---|---|---|---|
| 404 | `not_found` | 없는 안건 | 목록 새로고침 |
| 409 | `invalid_transition` | 이미 처리됨 (`posted`·`rejected`·`closed`) | "다른 곳에서 처리됨" 안내 + 새로고침 |
| 409 | `edit_not_allowed` | `approved`(게시 실패 후 재시도) 상태에서 다른 본문을 보냄. 고친 본문은 한 번만 확정 | 본문 없이 재시도 |
| 502 | `publish_failed` | 채널 게시 실패. 안건은 `approved` 에 남음 | "게시 실패 — 다시 시도" 버튼 (같은 approve 재호출 = 게시만 재시도) |

### 4.5 `POST /approvals/{id}/reject` — 거절

- 요청: `{ "reason": "릴리즈 날짜와 GPU 주소 빼 주세요" }` — `reason` 필수, 1자 이상
- 응답 `200`: `status: "rejected"` 인 `Approval`. **3번째 거절이면 `status: "closed"`** (더 이상 재작성 안 함)
- 이후: desk 가 몇 초 안에 사유를 반영한 새 초안을 올린다 → 폴링하면 같은 id 가 `pending`, `round + 1` 로 돌아온다

| 오류 | 뜻 |
|---|---|
| 404 `not_found` | 없는 안건 |
| 409 `invalid_transition` | `pending` 이 아님 |
| 422 (`ValidationError`, `loc: ["body","reason"]`) | 사유가 비었음 → 입력창 검증으로 막는다 |

## 5. 상태별 UI 규칙

| `status` | 배지 | 승인 | 거절 | 비고 |
|---|---|---|---|---|
| `pending` | 결재 필요 | ✅ (초안 편집 가능) | ✅ | `round > 1` 이면 "N번째 초안" + `rejections` 이력 표시 |
| `rejected` | 다시 쓰는 중 | — | — | 로딩 표시. 1분 넘게 머물면 desk 가 멈췄을 수 있음 |
| `approved` | 게시 중 / 게시 실패 | ✅ (재시도, 본문 없이) | — | approve 가 502 였을 때만 이 상태에 머문다. 초안 편집 잠금 |
| `posted` | 완료 | — | — | `posted_url` 링크 (`mock://` 이면 링크 대신 "기록만 됨") |
| `closed` | 닫힘 | — | — | 3회 거절로 종료 |

- `audience`: `public` → "사외", `company` → "사내" 등급 표시.
- `refusal` 이 있으면 head 가 답을 거부한 안건 → 초안이 "답하기 어렵다" 류일 가능성이 높다. 경고 표시 권장.
- `question`·`context[].text` 는 GitHub 마크다운(이미지, 코드, 표, `<details>`)일 수 있다 → **sanitize 한 뒤** 마크다운 렌더링 (예: `marked` + `DOMPurify`).

## 6. 최소 클라이언트

```ts
const BASE = "http://127.0.0.1:8790";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = typeof body?.detail === "string" ? body.detail : body?.detail?.[0]?.msg ?? res.statusText;
    throw Object.assign(new Error(msg), { status: res.status, code: body?.error });
  }
  return body as T;
}

export const listApprovals = (q: { status?: ApprovalStatus; channel?: Channel; task?: string } = {}) =>
  api<Approval[]>(`/approvals?${new URLSearchParams(q as Record<string, string>)}`);
export const getSummary = () => api<Summary>("/approvals/summary");
export const getApproval = (id: number) => api<Approval>(`/approvals/${id}`);
// editedDraft: 사람이 고친 본문 (고치지 않았으면 생략)
export const approve = (id: number, editedDraft?: string) =>
  api<Approval>(`/approvals/${id}/approve`, {
    method: "POST",
    ...(editedDraft
      ? { headers: { "content-type": "application/json" }, body: JSON.stringify({ draft: editedDraft }) }
      : {}),
  });
export const reject = (id: number, reason: string) =>
  api<Approval>(`/approvals/${id}/reject`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ reason }),
  });

// 폴링: 목록 + 건수를 3초마다 (참조 웹과 동일)
setInterval(async () => {
  const [items, summary] = await Promise.all([listApprovals({ status: "pending" }), getSummary()]);
  render(items, summary);
}, 3000);
```

## 7. 로컬에서 띄우기

```bash
cd RFA_module
./scripts/run_services.sh     # 결재 서버 :8790 + 지식 서버 :8791
./scripts/run_desk.sh         # (다른 터미널) 멘션 → 안건, 거절 → 재작성
curl -s http://127.0.0.1:8790/approvals/summary   # 떠 있는지 확인
```
