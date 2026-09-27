# 아키텍처 (v2)

## 1. 터미널 3개

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
| A. 결재 서버 (`services/approvals`) | `uvicorn --factory approvals.app:create_app --port 8790` | 8790 | 안건 저장·목록·승인/거절, 승인 시 게시. 브라우저 `:8790/` 참조 웹 | **웹 백엔드 = 이것.** 다영님 프런트가 부름. Step 3 |
| B. 지식 서버 (`services/head_stub`) | `uvicorn head_stub.app:app --port 8791` | 8791 | `POST /ask` 하나. `data/knowledge/` 에서 키워드로 찾아 답 | 민섭님 head agent 오면 안 켬 (`HEAD_URL` 만 변경) |
| C. desk (`workflow/rfa_workflow`) | `python -m rfa_workflow desk` | 없음 | 5초마다 GitHub/Slack 확인 → LangGraph 그래프 실행 → B 에 지식 요청, A 에 안건 제출. A 의 거절 안건을 폴링해 재작성 | 요청을 받지 않고 보내기만 함. Step 5·6 |

- **서버**(A, B)는 요청이 올 때만 일한다. **desk**(C)는 요청이 없어도 스스로 돈다 (`while True: tick(); sleep(5)`).
- LangGraph 그래프는 별도 프로세스가 아니라 desk 안에서 호출되는 함수다.
- `scripts/run_services.sh` 가 A+B 를, `scripts/run_desk.sh` 가 C 를 띄운다.

## 2. 모듈 단위

```
 바깥 세상                          이 PC (호스트)                                        다른 팀원
┌──────────┐      ┌─────────────────────────────────────────────────────────┐
│  GitHub  │      │  C. desk  (python -m rfa_workflow desk)                  │
│  이슈    │◄────►│  ┌───────────────────────────────────────────────────┐  │
│ @login   │ 폴링 │  │ channels/github.py   channels/slack.py            │  │
└──────────┘      │  │   (멘션 폴링)          (Socket Mode 수신)           │  │
┌──────────┐      │  └───────────┬───────────────────┬───────────────────┘  │
│  Slack   │◄────►│              ▼   Mention          ▼                       │
│ @rfa-desk│ 소켓 │  ┌───────────────────────────────────────────────────┐  │
└──────────┘      │  │  LangGraph  graph.py  (대응 에이전트, 채널 무관)     │  │
                  │  │   intake → ask_head → write(LLM) → submit          │  │
                  │  └──────┬──────────────────────────────┬─────────────┘  │
                  │         │ POST /ask                     │ POST /approvals │
                  │         ▼                               ▼                 │
                  │  B. head_stub :8791              A. approvals :8790       │
                  │  ┌──────────────────┐          ┌───────────────────────┐ │      ┌──────────────┐
                  │  │ POST /ask        │          │ 결재 API + 상태기계     │◄┼──────┤ 웹 프런트     │
                  │  │ data/knowledge/  │          │ pending→approved→posted│ │ REST │ (다영님)      │
                  │  │ 에서 검색해 답함  │          │ pending→rejected       │ │      │              │
                  │  └──────────────────┘          │ 승인 시 채널에 게시     │ │      └──────────────┘
                  │   ↑ 민섭님 head agent 로        └───────────┬───────────┘ │
                  │     교체. 검열은 그 안에서                   ▼             │
                  │                                   channels/*.post()  ─────┼──► GitHub 댓글 / Slack 답글
                  └─────────────────────────────────────────────────────────┘
```

| 경로 | 무엇 | 단계 |
|---|---|---|
| `common/rfa_common/contracts.py` | 계약 모델. `contracts/*.openapi.yaml` 과 1:1 (테스트가 대조) | 1 |
| `contracts/head.openapi.yaml` | desk → head agent | 1 |
| `contracts/approvals.openapi.yaml` | 프런트·desk → 결재 서버 | 1 |
| `services/head_stub/` | `POST /ask` stub | 2 |
| `services/channels/github.py` | GitHub 멘션 찾기(폴링), 스레드 읽기, 댓글 달기 | 2 (Step 4 에서 공통 인터페이스) |
| `services/channels/slack.py` | Slack Socket Mode 수신, 스레드 읽기, 답글 | 4 |
| `services/approvals/` | 결재 서버 + 참조 웹 | 3 |
| `workflow/rfa_workflow/graph.py` | 대응 에이전트 그래프 | 5 |
| `workflow/rfa_workflow/desk.py` | 상주 루프 | 6 |

## 3. 흐름: Slack 에서 "@rfa-desk ORBIT 벤치마크 어때?"

| # | 어디 | 누가 판단 | 결과 |
|---|---|---|---|
| 1 | Slack | 사람 | `#rfa-test` 에 멘션 |
| 2 | desk | 코드 | Socket Mode 로 이벤트 수신 → `Mention` |
| 3 | desk → head | 코드 | `POST /ask` {질문, slack, company, 스레드, 맥락, feedback=[]} |
| 4 | head | head agent | 업무(orbit) 선택, 검열된 knowledge 반환 |
| 5 | desk | LLM (writer) | Slack 말투 초안 |
| 6 | desk → A | 코드 | `POST /approvals` → 안건 #12 `pending` |
| 7 | 프런트 | 사람 | 초안 확인 → **거절** "릴리즈 날짜가 들어가 있음" → `rejected` |
| 8 | desk | 코드 | 다음 틱에 `rejected` 발견 → 3번을 feedback=[{초안, 사유}] 로 다시 |
| 9 | desk → A | 코드 | `POST /approvals/12/revise` → `pending`, round 2 |
| 10 | 프런트 | 사람 | **승인** |
| 11 | A | 코드 | `approved` → Slack 스레드에 답글 → `posted` |

3번 거절되면 `closed` 로 닫히고 더 이상 다시 쓰지 않는다.

## 4. 경계와 보안

- 이 레포에는 **검열이 없다.** head agent 가 돌려준 knowledge 는 검열이 끝난 것으로 취급한다. stub 은 검열하지 않으므로 데모 지식의 기밀이 초안에 흘러들고, 사람이 거절하는 장면이 된다.
- 게시는 결재 서버만 한다. desk 에는 게시 경로가 없다 (채널의 `post()` 를 부르는 곳은 approvals 뿐).
- 결재 API 에는 인증이 없다. 에이전트가 승인을 부르지 못하게 막는 것은 샌드박스 네트워크 정책(다영님).
- 그래프는 요청 사이에 기억을 남기지 않는다 (실행마다 새 상태). 앞 요청의 지식이 다른 채널의 답에 섞이지 않는다.
- 게시한 GitHub 답글에는 보이지 않는 `<!-- rfa-bot -->` 표시를 붙여, 멘션 폴링이 자기 답글에 다시 반응하지 않게 한다. Slack 은 봇 자신의 메시지를 이벤트에서 거른다 (Step 4).

## 5. 팀 통합

- 민섭님: `contracts/head.openapi.yaml` 을 구현하면 `HEAD_URL` 만 바꾼다.
- 다영님: `contracts/approvals.openapi.yaml` 의 `frontend` 태그 5개를 부른다. 참조 웹 `:8790/` 이 같은 API 를 쓰므로 동작 예시로 볼 수 있다.
