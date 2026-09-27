# 셋업과 재현 (v2)

> 단계가 진행되면서 채워진다. 지금은 Step 5·4 까지 (결재 서버 + head_stub + 대응 에이전트 그래프 + GitHub 채널).

## 전제

- Python 3.12+, `uv`.
- GitHub fine-grained 토큰: 감시할 테스트 레포 한정, Issues read/write.
- Slack 앱 (Step 4 부터): `person/TODO.md` 의 절차대로 만들고 봇 토큰(`xoxb-`)과 앱 토큰(`xapp-`)을 받는다.

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

결재 웹: http://127.0.0.1:8790/ — 안건은 desk 가 만든다 (Step 6). 그 전에는 대응 에이전트를 손으로 돌린다:

```bash
M='{"channel":"slack","target":"C0123ABC/1727000000.000100","author":"product-team",
    "text":"ORBIT 벤치마크 진행 어때요?","url":"https://slack.com/archives/C0123ABC/p1727000000000100",
    "created_at":"2026-09-27T10:00:00Z"}'
uv run --env-file .env python -m rfa_workflow run --mention-json "$M"   # → 안건 #1 pending
# 결재 웹에서 거절(사유 "릴리즈 날짜가 들어가 있음") 후
uv run --env-file .env python -m rfa_workflow redo 1                   # → 안건 #1 round 2
```
`RFA_LLM_MODE=mock` 이면 키 없이, `anthropic` 이면 Claude 가 답을 쓴다.

실제 GitHub 에 게시하려면 결재 서버를 `RFA_PUBLISHER=live RFA_CHANNELS=github` 로 띄운다 (`GITHUB_*` 필요). 승인하면 안건의 이슈에 댓글이 달린다. 멘션을 자동으로 받아 오는 것은 Step 6 (desk).

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
