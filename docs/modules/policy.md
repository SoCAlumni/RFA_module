# policy — 기밀 기준과 사람 피드백

## 역할

censor 노드가 판단할 때 읽는 기준. 회의록의 "공식 기밀관리 문서(official)"와 "개인적 필요/선호(personal)", 그리고 Human-in-the-loop 피드백을 파일로 관리한다. review 서비스가 `GET /policy/{scope}`로 합쳐서 준다.

## 파일

```
data/policy/
├─ public/
│  ├─ official.md      # 대외 공식 기준 (예: 미공개 릴리즈 일자, 미공개 모델명, 내부 벤치마크 수치)
│  └─ personal.md      # 사용자 개인 기준 (예: 몰래 쓰는 GPU pool 언급 금지)
├─ internal/           # (Step 12+) 없으면 GET /policy/internal 은 404
│  ├─ official.md      # 사업부/회사 내 기준 (public보다 느슨)
│  └─ personal.md      # 없으면 빈 문자열
├─ feedback.jsonl      # 사람 거절/승인 사례 (scope 필드로 구분). 런타임에 쌓이므로 gitignore
├─ internal_hosts.txt  # 스캐너용 사내 호스트/도메인
└─ internal_paths.txt  # 스캐너용 사내 경로 접두사
```

## 기준 문서 형식 (`official.md`, `personal.md`)

```markdown
# Public 채널 공식 기밀 기준
## 규칙
- [release-date] 출시/릴리즈 예정일은 공식 발표 전까지 비공개. 대체 표현: "일정은 확정 후 공유".
- [model-name] 미공개 모델 코드명(Nimbus2 등)은 비공개. 대체: "차기 모델".
- [internal-metric] 내부 벤치마크 수치(EM, 지연 등)는 비공개. 대체: "소폭 하락/개선" 수준의 정성 표현.
## 허용
- 공개 논문에 있는 수치, 공개 레포 링크.
```
규칙 id(`[release-date]`)는 censor verdict의 `reasons[].rule`에 `official:release-date`로 붙는다.

## feedback.jsonl

```json
{"at":"2026-09-26T13:02:00","scope":"public","review_id":12,"decision":"reject","reason":"QAT 검토 중이라는 사실도 아직 밖에 말하면 안 됨","draft_excerpt":"...QAT를 검토 중...","rule_hint":"official:roadmap"}
{"at":"...","scope":"public","review_id":13,"decision":"approve","reason":null}
```
- reject는 전부, approve는 verdict가 redact였던 것만 기록(어떤 삭제가 적절했는지 예시).
- `GET /policy/public`은 같은 scope 중 reject 먼저, 그 안에서 최신순으로 최대 10건을 few-shot으로 포함. 형식이 깨진 줄은 건너뛴다.

## censor 프롬프트 주입 형태

```
[공식 기준] official.md 본문
[개인 기준] personal.md 본문
[과거 결정 사례] feedback 최근 10건 (reject 우선)
[스캐너 결과] scan[]
[근거 문서] sources[] (어느 문장이 어디서 왔는지)
[초안] draft
→ Verdict JSON
```

## 다른 모듈과의 연결

| 상대 | 방향 |
|---|---|
| review 서비스 `policy.py` | 읽기, feedback 추가 |
| censor 노드 | `GET /policy/{scope}` |
| 결재 웹 / 다영님 UI | `POST /policy/{scope}/personal` (9/28) |

## 할 일

- [ ] public/official.md, personal.md 초안 (데모 시나리오에 맞춰 규칙 4~5개)
- [ ] internal_hosts.txt, internal_paths.txt
- [ ] feedback 기록/읽기
- [ ] 9/27 internal 기준
- [ ] 9/28 personal 추가 API + 화면

## 미정

- 개인 기준을 사용자가 채팅(main 에이전트)으로 추가하게 할지("이건 밖에 말하지 마") → 하면 좋은 데모지만 9/28 여유 있을 때.
- feedback이 쌓였을 때 official.md에 규칙으로 승격하는 절차(사람이 수동 편집으로 시작).
