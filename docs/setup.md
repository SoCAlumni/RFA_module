# 셋업과 재현 (v2)

> 단계가 진행되면서 채워진다. 지금은 Step 2 까지 (head_stub 만 실행 가능).

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
./scripts/run_services.sh          # head_stub(8791). Ctrl+C 로 종료. 로그: data/state/logs/
```

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

API 문서: 서버가 떠 있으면 http://127.0.0.1:8791/docs. 계약 전체는 https://socalumni.github.io/RFA_module/.

## GitHub 공유

- 올림: 코드, docs, contracts, scripts, data/knowledge, `.env.example`.
- 안 올림: `.env`, `data/state/`, `reference/`, 인증서.
