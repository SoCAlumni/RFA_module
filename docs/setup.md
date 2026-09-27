# 셋업과 재현 (v2)

> 지금은 Step 7 까지 (결재 서버 + head_stub + 대응 에이전트 + GitHub·Slack 채널 + desk).

## 전제

- Python 3.12+, `uv`.
- GitHub fine-grained 토큰: 감시할 테스트 레포 한정, Issues read/write.
- Slack 앱 (Step 7 부터): `person/TODO.md` 의 절차대로 만들고 유저 토큰(`xoxp-`)과 앱 토큰(`xapp-`)을 받는다. 봇 계정이 아니라 내 계정으로 동작한다.

## 설치와 테스트

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```

## 실행

```bash
cp .env.example .env && $EDITOR .env
./scripts/run_services.sh          # approvals(8790) + head_stub(8791). Ctrl+C 로 종료. 로그: data/state/logs/
```

v1 에서 쓰던 `.env` 라면 `RFA_PUBLISHER=github` 를 `RFA_PUBLISHER=mock` 으로 바꾼다 (v2 는 `mock` 만 안다. 실제 게시 `live` 는 Step 4).

결재 웹: http://127.0.0.1:8790/ — 안건은 desk 가 만든다 (아래 "desk"). 대응 에이전트를 손으로 한 번만 돌려 볼 수도 있다:

```bash
M='{"channel":"slack","target":"C0123ABC/1727000000.000100","author":"product-team",
    "text":"ORBIT 벤치마크 진행 어때요?","url":"https://slack.com/archives/C0123ABC/p1727000000000100",
    "created_at":"2026-09-27T10:00:00Z"}'
uv run --env-file .env python -m rfa_workflow run --mention-json "$M"   # → 안건 #1 pending
# 결재 웹에서 거절(사유 "릴리즈 날짜가 들어가 있음") 후
uv run --env-file .env python -m rfa_workflow redo 1                   # → 안건 #1 round 2
```
`RFA_LLM_MODE=mock` 이면 키 없이, `anthropic` 이면 Claude 가 답을 쓴다.

## desk (계속 돌리기)

```bash
./scripts/run_services.sh     # 터미널 1: 결재 서버 + head_stub
./scripts/run_desk.sh         # 터미널 2: 5초마다 RFA_CHANNELS 의 멘션을 받아 결재함에, 거절된 안건은 다시 씀
./scripts/run_desk.sh --once  # 한 틱만
```

- 감시 레포 이슈에 `@<RFA_GITHUB_LOGIN>` 멘션을 달면 다음 틱에 결재함(http://127.0.0.1:8790/)에 안건이 뜬다.
- 거절하면 다음 틱에 사유를 반영한 새 초안이 올라온다. 실패한 작업은 30초·60초 뒤 다시, 3번째 실패에서 포기한다.
- 실제 GitHub 에 게시하려면 `.env` 를 `RFA_PUBLISHER=live` 로 바꾸고 run_services.sh 를 다시 띄운다. 승인하면 그 이슈에 **내 계정 이름으로** 댓글이 달린다 (숨김 표시 `<!-- rfa-bot -->` 가 붙어 desk 가 자기 답글에 다시 반응하지 않는다).
- 이미 본 멘션 기록은 `data/state/mentions_seen.json`. 지우면 최근 24시간 멘션을 다시 가져온다.

## Slack 켜기

1. `person/TODO.md` 1장대로 Slack 앱을 만들고 `.env` 에 `SLACK_USER_TOKEN`(xoxp-), `SLACK_APP_TOKEN`(xapp-) 을 넣는다.
2. `.env` 의 `RFA_CHANNELS=github,slack` 으로 바꾸고 두 스크립트를 다시 띄운다. desk 로그에 `slack: U… 로 이벤트 받는 중` 이 뜨면 연결된 것.
3. **다른 계정**이 내가 들어가 있는 채널에서 `@나 ORBIT 벤치마크 어때요?` 라고 쓰거나 나에게 DM 한다 → 결재함에 안건 → 승인하면 그 스레드에 **내 이름으로** 답글.

- 비서는 **내가 보낸 메시지를 전부 무시**한다 (비서 답글도 내 이름이라 이 규칙으로 루프를 막음). 그래서 셀프 멘션으로는 테스트할 수 없다.
- Slack 은 **desk 가 연결돼 있는 동안 온 멘션만** 확실히 받는다 (꺼져 있을 때 온 것은 놓칠 수 있다). GitHub 는 다음 폴링 때 지난 멘션도 가져온다.
- Socket 연결은 desk 만 한다. 결재 서버는 게시만 하므로 연결하지 않는다.

head_stub 확인:
```bash
curl -s -X POST http://127.0.0.1:8791/ask -H 'content-type: application/json' -d '{
  "question": "ORBIT 벤치마크 진행 어때?",
  "channel": "github", "audience": "public",
  "target": "zetwhite/rfa-test#1", "url": "https://github.com/zetwhite/rfa-test/issues/1",
  "requester": "someone",
  "feedback": [{"draft": "…", "reason": "릴리즈 날짜 빼줘", "at": "2026-09-27T09:00:00Z"}]
}'
```
`feedback` 를 빼고 보내면 `11/3` 릴리즈 문장이 knowledge 에 들어 있고, 넣으면 빠진다.

API 문서: 서버가 떠 있으면 http://127.0.0.1:8790/docs, http://127.0.0.1:8791/docs. 계약 전체는 https://socalumni.github.io/RFA_module/.

## GitHub 공유

- 올림: 코드, docs, contracts, scripts, data/knowledge, `.env.example`.
- 안 올림: `.env`, `data/state/`, `reference/`, 인증서.
