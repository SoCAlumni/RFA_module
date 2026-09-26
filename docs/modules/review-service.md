# review 서비스 (호스트)

## 역할

결재 문서의 단일 진실 원천. 상태기계로 순서를 강제하고, 비밀값 스캔과 서명 토큰 발급, 알람/결재 웹, 최종 게시를 담당한다. 이 모듈의 "게이트"다.

## 입출력 계약 (OpenAPI: `contracts/review.openapi.yaml`)

| 메서드 | 경로 | 호출자 | 전이 | 설명 |
|---|---|---|---|---|
| POST | `/reviews` | LangGraph intake | → `opened` | `{channel, target, question, requester, source_url}` |
| GET | `/reviews?status=` | 웹, UI, desk | — | 목록 |
| GET | `/reviews/{id}` | 모두 | — | 문서 전체 (events 포함) |
| POST | `/reviews/{id}/knowledge` | LangGraph | `opened → knowledge_ready` | `{answer, sources[]}` |
| POST | `/reviews/{id}/draft` | LangGraph submit | `knowledge_ready → drafted → scanned` | `{text, edit_log[]}`. 서버가 스캔 자동 실행 |
| POST | `/reviews/{id}/verdict` | LangGraph censor | `scanned → reviewed` | `{verdict, redacted_body, reasons[], items[]}` |
| POST | `/reviews/{id}/needs-human` | LangGraph | `* → needs_human` | 답변 불가 사유 |
| POST | `/reviews/{id}/approve` | **사람 (loopback만)** | `reviewed → approved → posted` | 서명 → 게시 |
| POST | `/reviews/{id}/reject` | **사람 (loopback만)** | `reviewed → rejected` | `{reason}` → feedback.jsonl |
| GET | `/policy/{scope}` | censor 노드 | — | scope=public\|internal. official + personal + feedback |
| POST | `/policy/{scope}/personal` | 사람/UI | — | 개인 기준 추가 (9/28) |
| GET | `/` | 브라우저 | — | 알람/결재 웹 |

순서 위반은 `409 {error: "invalid_transition", from, to}`.

## 데이터

`data/state/review-<id>.json`
```json
{
  "id": 12, "status": "reviewed", "channel": "public",
  "target": "zetwhite/rfa-test#34", "source_url": "...", "requester": "someone",
  "question": "TRIV3 벤치마크 진행 어때?",
  "knowledge": {"task_id": "triv3", "answer": "...", "sources": [...]},
  "draft": "...", "edit_log": [{"round": 1, "verdict": "revise", "notes": "..."}, {"round": 2, "verdict": "pass"}],
  "scan": [{"type": "private_ip", "match": "10.12.3.4", "span": [88, 97]}],
  "verdict": {"verdict": "redact", "redacted_body": "...", "reasons": [{"rule": "official:release-date", "span": "11/3 릴리즈"}]},
  "final_body": "...",
  "decision": {"by": "human", "at": "...", "reason": null},
  "events": [{"at": "...", "who": "intake", "what": "opened"}, ...]
}
```

상태기계:
```
opened → knowledge_ready → drafted → scanned → reviewed → approved → posted
                                                        └→ rejected
(어느 단계에서든) → needs_human
```
`final_body` = verdict가 redact면 `redacted_body`, allow면 `draft`. block이면 approve 불가.

## 스캐너 (`scanner.py`)

정규식 + 목록 기반, LLM 없음.

| type | 패턴 |
|---|---|
| token | `hf_[A-Za-z0-9]{20,}`, `ghp_…`, `github_pat_…`, `sk-…`, `AKIA…`, `xox[bp]-…` |
| private_ip | `10.x.x.x`, `172.16-31.x.x`, `192.168.x.x` |
| internal_host | `data/policy/internal_hosts.txt`의 도메인/호스트명 |
| internal_path | `/nfs/`, `//sr-`, `C:\\Users\\` 등 `data/policy/internal_paths.txt` |

결과는 censor에게 "참고"로 전달되며, block 여부는 censor가 정한다. 단, `token`이 남아 있는 final_body는 approve 시 서버가 거부한다.

## 서명 토큰 (`clearance.py`)

```
payload = {review_id, target, body_sha256, exp}
token   = base64(payload) + "." + HMAC_SHA256(RFA_CLEARANCE_KEY, payload)
```
`post_comment(target, body, token)`는 서명, 만료, `sha256(body) == body_sha256`, `target` 일치를 확인한다. 키는 호스트 환경변수에만 있다.

## 알람/결재 웹 (`static/index.html`)

프레임워크 없음. 3초마다 `GET /reviews`.

- 왼쪽: 목록. 상태 배지(진행 중 / 결재 대기 / 게시됨 / 거절 / 사람 필요). 새 항목은 강조.
- 오른쪽 타임라인: 질문 원문 → 지식(answer, sources) → 초안 → 첨삭 이력 → 스캔 결과(하이라이트) → censor 판정(원본↔수정안 나란히, 사유별 기준 태그 official/personal/scanner) → 결재 버튼.
- 승인 버튼 → `POST /approve`. 거절은 사유 입력 필수.

## 파일 구조

```
services/review/
├─ app.py            # FastAPI 앱, 라우트
├─ models.py         # pydantic: Review, Verdict, Event, KnowledgeResult
├─ store.py          # json 파일 저장, transition(id, from, to)
├─ scanner.py
├─ clearance.py
├─ policy.py         # official/personal/feedback 읽기, feedback 추가
├─ publisher.py      # 승인 후 채널별 게시 (github → mcp_channels 내부 함수 호출)
└─ static/index.html
```

## 다른 모듈과의 연결

| 상대 | 방향 | 무엇 |
|---|---|---|
| LangGraph | 들어옴 | reviews API (intake, knowledge, draft, verdict), policy |
| mcp_channels/github | 나감 | `post_comment(target, body, token)` (Python 내부 호출) |
| 다영님 UI | 들어옴 | 같은 REST로 화면 대체 |
| policy 파일 | 읽기/쓰기 | `data/policy/**` |

## 할 일

- [ ] models, store, transition + 409
- [ ] scanner + 테스트 (예시 토큰/IP/호스트 검출)
- [ ] clearance sign/verify + 테스트 (위조, 만료, 본문 변경 거부)
- [ ] 라우트 전체, approve의 loopback 검사
- [ ] policy 읽기(`GET /policy/{scope}`), reject → feedback.jsonl
- [ ] index.html 타임라인 + 버튼
- [ ] publisher → github post_comment

## 미정

- 결재 웹에서 사람이 수정안을 직접 편집하고 승인할지 (편집하면 해시가 바뀌므로 서명은 편집본 기준으로 발급). 9/27 판단.
- `needs_human` 문서의 후속 처리 UI.
