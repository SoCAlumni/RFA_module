# BOOTSTRAP — 이 레포에 처음 온 사람·AI를 위한 진입점

> 팀원의 AI 어시스턴트가 읽는 것을 가정하고 쓴 문서다. 여기서 길을 찾고, 상세는 링크된 문서를 읽어라.
> 모든 경로는 레포 루트 기준. 모든 명령은 복붙해서 그대로 실행된다.

## 1. 이 레포가 뭔가

NVIDIA 해커톤 팀 프로젝트(개인 비서 멀티에이전트)의 **대응 에이전트 모듈**이다.
GitHub·Slack에서 "나"에게 온 멘션·DM을 받아 → head agent에게 검열된 지식을 받고 → Claude가 채널 말투로 답을 쓰고 → **사람 결재**를 거쳐 → 그 채널에 내 이름으로 답글을 단다. 거절되면 사유를 반영해 다시 쓴다.

```
   GitHub / Slack
        ▲ ▼ (읽기)
  ┌──────────────┐   "지식 줘"     ┌──────────────┐
  │ C. desk      │ ─────────────► │ B. 지식 서버  │  ← 민섭님 head agent 로 교체
  │ (감시+작성)   │     :8791      └──────────────┘
  │              │   "결재 올려줘"  ┌──────────────┐        ┌────────────┐
  │              │ ─────────────► │ A. 결재 서버  │ ◄───── │ 다영님 프런트 │
  │              │     :8790      │  (웹 백엔드)  │  REST  │  (브라우저)  │
  └──────────────┘ ◄───────────── └──────┬───────┘        └────────────┘
                    "거절된 거 있어?"      │ 승인되면 게시
                                         ▼
                                   GitHub / Slack
```

상세 구조(모듈 단위, 흐름 표, 보안 경계)는 [docs/architecture.md](docs/architecture.md).

## 2. 당신이 누구인가 — 역할별 읽기 경로

| 당신 | 할 일 | 이 순서로 읽어라 | 바꾸는 것 |
|---|---|---|---|
| **head agent 구현 AI** (민섭님 쪽) | `POST /ask`를 계약대로 구현 | [docs/contracts.md](docs/contracts.md) §1 → [contracts/head.openapi.yaml](contracts/head.openapi.yaml) → 참고 구현 [services/head_stub/app.py](services/head_stub/app.py) → 아래 §6 검증법 | 이 레포는 안 바꿈. 완성되면 우리 `.env`의 `HEAD_URL` 한 줄만 교체 |
| **프런트엔드 AI** (다영님 쪽) | `frontend` 태그 API 5개 호출 | [docs/contracts.md](docs/contracts.md) §2 → [contracts/approvals.openapi.yaml](contracts/approvals.openapi.yaml) → 살아있는 예시 [services/approvals/static/index.html](services/approvals/static/index.html) (같은 API를 쓰는 참조 웹) | 이 레포는 안 바꿈. 다른 포트·기기에서 불러도 됨 (CORS `RFA_CORS_ORIGINS`, 기본 `*`) |
| **전체 재현·데모** | 띄우고 한 바퀴 돌리기 | 아래 §4 → [docs/setup.md](docs/setup.md) → 토큰은 [docs/tokens.md](docs/tokens.md) | `.env` |
| **이 레포를 고치는 AI** | 코드 수정 | [docs/develop_plan.md](docs/develop_plan.md)의 **현재 상태**와 **규칙** → [docs/architecture.md](docs/architecture.md) → 결정 근거 [docs/plan.md](docs/plan.md) | — |

## 3. API는 여기를 봐라 (우선순위 순)

1. **[docs/contracts.md](docs/contracts.md)** — 팀 경계 API 두 개(`/ask`, `/approvals`)의 한 장 요약. 누가 부르고, 요청·응답 예시 JSON, 상태도.
2. **Redoc**: https://socalumni.github.io/RFA_module/ — 계약 yaml에서 자동 배포되는 브라우저 문서.
3. **원본**: [contracts/head.openapi.yaml](contracts/head.openapi.yaml), [contracts/approvals.openapi.yaml](contracts/approvals.openapi.yaml). 계약을 바꿀 땐 이 yaml을 고친다 (main 머지 시 Redoc 자동 갱신).
4. **살아있는 예시** — 문서보다 확실하다:
   - 결재 API를 실제로 쓰는 화면: [services/approvals/static/index.html](services/approvals/static/index.html) (서버 켜면 `http://127.0.0.1:8790/`)
   - 요청 본문 예시 dict: [services/tests/test_approvals.py](services/tests/test_approvals.py)의 `GITHUB`/`SLACK`
   - `/ask` 요청을 만드는 쪽: [workflow/rfa_workflow/graph.py](workflow/rfa_workflow/graph.py)의 `ask_head`
5. **계약↔코드가 어긋나면 테스트가 잡는다**: [services/tests/test_contracts.py](services/tests/test_contracts.py) (yaml properties·enum ↔ pydantic 모델 자동 대조), `test_approvals.py`의 `test_app_routes_match_contract` (서버 경로·태그 ↔ yaml). 계약을 바꾸면 이 테스트부터 깨진다 — 의도된 동작이다.

파이썬에서 계약 모델을 쓰려면: `from rfa_common.contracts import Mention, AskRequest, Approval, ...` ([common/rfa_common/contracts.py](common/rfa_common/contracts.py)).

## 4. 5분 실행 — 빈 컴퓨터에서, 토큰 0개로

전제: Linux 또는 macOS (Windows는 WSL). git만 있으면 된다. **계정·토큰이 하나도 필요 없다** — 기본 설정이 mock 모드(초안은 규칙으로 작성, 게시는 기록만)다.

```bash
# 1) uv 설치 (Python 3.12도 uv가 알아서 받는다)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2) 레포 받기·설치·테스트
git clone https://github.com/SoCAlumni/RFA_module.git && cd RFA_module
uv sync
uv run pytest -q            # 156 passed 가 나와야 한다

# 3) 설정 만들고 띄우기 (기본값 그대로 = mock, 토큰 불필요)
cp .env.example .env
./scripts/run_demo.sh       # 결재 서버(:8790) + 지식 서버(:8791) + desk 를 한 번에. Ctrl+C 로 종료
```

다른 터미널에서 질문 하나를 넣어 본다 (채널 토큰이 없으니 멘션을 CLI로 주입):

```bash
uv run --env-file .env python -m rfa_workflow run --mention-json '{
  "channel":"slack","target":"C0123ABC/1727000000.000100","author":"product-team",
  "text":"ORBIT 벤치마크 진행 어때요?","url":"https://slack.com/archives/C0123ABC/p1727000000000100",
  "created_at":"2026-09-27T10:00:00Z"}'
```

그다음 브라우저에서 **http://127.0.0.1:8790/** 을 연다:
안건 #1의 초안에 기밀(11/3 릴리즈, GPU 주소)이 보인다 → 사유 "릴리즈 날짜와 GPU 주소 빼 주세요"로 **거절** → 5초 안에 desk가 다시 쓴 2번째 초안이 온다(기밀 빠짐) → **승인** → `mock://...`으로 게시 기록.

이게 전체 흐름이다. 실제 GitHub·Slack·Claude로 승격하려면 → [docs/tokens.md](docs/tokens.md).

## 5. 무엇이 진짜고 무엇이 대역(stub)인가

| 부분 | 상태 | 비고 |
|---|---|---|
| 대응 에이전트 그래프, desk, 결재 서버 | **진짜** | GitHub·Slack 실 E2E 완료 (2026-09-27) |
| GitHub·Slack 채널 | **진짜** | Slack은 봇이 아니라 "나"(User Token)로 동작 |
| 지식 서버 `services/head_stub/` | **대역** | 키워드 매칭. **검열 안 함** — 그래서 첫 초안에 기밀이 섞여 사람이 거절하는 장면이 된다. 질문을 `ORBIT벤치마크`처럼 붙여 쓰면 업무를 못 찾는 한계 있음. 민섭님 head agent가 대체 |
| 기밀 검열 | **이 레포에 없음** | head agent(민섭님) 소관. `/ask` 응답의 knowledge는 검열이 끝난 것으로 취급한다 |
| 결재 웹 `:8790/` | **참조용** | 다영님 프런트가 대체. API 사용 예시로 유지 |

## 6. head agent를 구현했다면 — 이렇게 검증해라

1. 자기 서버를 띄우고, 이 레포 `.env`의 `HEAD_URL`을 그 주소로 바꾼다 (그게 전부다).
2. 계약 케이스는 [services/tests/test_head_stub.py](services/tests/test_head_stub.py)의 `ask()` 헬퍼가 보내는 본문 참고 — 특히 `feedback`(거절 이력)이 있을 때 그 사유가 다시 안 생기게 지식을 다시 주는지.
3. `./scripts/run_demo.sh`로 전체를 띄우고 §4의 CLI 주입 → 결재 웹에서 거절 → 재작성 → 승인이 한 바퀴 돌면 통합 완료.

## 7. 문서 지도 — 이 질문이면 이 문서

| 질문 | 문서 |
|---|---|
| 왜 이렇게 설계했나 (결정과 이유) | [docs/plan.md](docs/plan.md) |
| 구조·흐름·보안 경계 | [docs/architecture.md](docs/architecture.md) |
| API 계약 | [docs/contracts.md](docs/contracts.md) + Redoc |
| 실행·데모 방법 | [docs/setup.md](docs/setup.md) |
| 토큰은 어디서 어떻게 만드나 | [docs/tokens.md](docs/tokens.md) |
| 지금 어디까지 됐나, 다음 뭐 하나 | [docs/develop_plan.md](docs/develop_plan.md) 맨 위 "현재 상태" |
| v1 코드는 어디 갔나 | [docs/migration.md](docs/migration.md) (열람: `git show 819053f:<경로>`) |
| 사람이 직접 해야 하는 일 | [person/TODO.md](person/TODO.md) |

## 8. 이 레포에서 작업하는 규칙 (요약)

- 한 단계 = 재현 가능한 기능 하나 = PR 하나. PR은 저장소 주인이 리뷰·머지한다.
- PR 전에 `uv run pytest -q`와 `uv run ruff check . && uv run ruff format --check .` 통과 + 셀프 코드리뷰. 체크리스트와 상세 규칙: [docs/develop_plan.md](docs/develop_plan.md) "규칙" 절.
