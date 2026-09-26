# knowledge stub (호스트) — 실무대장 계약

## 역할

민섭님의 실무대장(Task supervisor)이 제공할 API를 **계약대로 흉내**낸다. 언론사 writer는 지식을 이 창구로만 얻는다. 진짜 실무대장이 생기면 `KNOWLEDGE_URL`만 바꾼다.

## 입출력 계약 (OpenAPI: `contracts/knowledge.openapi.yaml`)

| 메서드 | 경로 | 요청 | 응답 |
|---|---|---|---|
| GET | `/tasks` | — | `[{id, name, description, updated_at}]` 지금 살아 있는 task 목록 (동적) |
| POST | `/tasks/{task_id}/ask` | `{question}` | `{task_id, answer, confidence, sources: string[]}` |

`sources`는 근거를 한 줄씩 적은 문자열 배열이다 (예: `["TRIV3 9월 진행 현황: INT4 이후 소폭 하락"]`). 근거가 없으면 `[]`.

설계 의도: "DB 검색"이 아니라 **"task supervisor에게 질문"**. 뒤에서 task 에이전트가 동적으로 생성되든 토론을 하든, 호출자는 answer와 sources만 받는다 (sub-agent as a tool). `sources`는 editor의 근거 대조와 censor의 출처 추적에 필요하므로 필수.

## stub 동작

- `data/knowledge/<task_id>/_task.yaml`: `{name, description}` → `/tasks`
- `data/knowledge/<task_id>/*.md`: 지식 문서. frontmatter `{title, tags, updated_at}`
- `ask`: 질문과 각 md의 키워드 겹침으로 상위 k=3 선택 → 본문을 이어 붙여 `answer` (LLM 없이 요약 문단 그대로). `confidence`는 겹침 비율. 선택 문서마다 `"제목: 요약"` 한 줄을 `sources`에.
- LLM을 쓰지 않는 이유: stub은 계약 확인용이고, 데모에서 기밀이 "그대로" 초안에 흘러 들어가야 censor가 거르는 장면이 나온다.

데모용 stub 데이터(9/26):

| task | 문서 | 일부러 넣는 기밀 |
|---|---|---|
| triv3 | progress-2026-09.md | 미공개 모델명 Gauss4, JGA 0.5%p(official), 11/3 릴리즈(official), GPU pool 10.12.3.4(personal + scanner), `hf_…` 토큰(scanner) |
| quantization | qat-notes.md | 없음 (대조군) |
| quest | design.md | 사내 경로 `/nfs/quest/` (scanner) |

## 파일 구조

```
services/knowledge_stub/
├─ app.py        # FastAPI
├─ loader.py     # md + frontmatter 읽기
└─ rank.py       # 키워드 겹침 점수
data/knowledge/<task_id>/_task.yaml, *.md
```

## 다른 모듈과의 연결

| 상대 | 방향 |
|---|---|
| LangGraph `ask_knowledge` 노드 | 들어옴 (`GET /tasks`, `POST /tasks/{id}/ask`) |
| 민섭님 실무대장 | 이 계약을 구현하면 교체 |

## 교체 절차 (민섭님)

1. `contracts/knowledge.openapi.yaml`대로 두 엔드포인트 구현.
2. `.env`의 `KNOWLEDGE_URL`을 실무대장 주소로 변경.
3. 샌드박스 정책(`policies/rfa.yaml`)에 그 주소 허용 추가.

## 할 일

- [ ] OpenAPI yaml
- [ ] stub 앱 + 데모 데이터 3 task
- [ ] 테스트: `/tasks` 목록, `ask`가 sources를 돌려주는지

## 미정

- 실무대장 주소가 샌드박스 안일지 호스트일지.
