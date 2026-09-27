# RFA_module — 대응 에이전트 + 채널 + 결재 백엔드

NVIDIA Agentic AI 해커톤 팀 프로젝트(개인 비서 멀티에이전트)의 한 모듈.
GitHub·Slack 에서 멘션을 받아 → head agent 에게 검열된 지식을 받고 → 답을 써서 사람 결재에 올리고 → 승인되면 그 채널에 답글을 단다. 거절되면 사유를 들고 다시 쓴다.

- 계획과 결정: [docs/plan.md](docs/plan.md)
- 구조: [docs/architecture.md](docs/architecture.md)
- **팀 경계 API**: [docs/contracts.md](docs/contracts.md) · Redoc https://socalumni.github.io/RFA_module/ (`contracts/*.openapi.yaml` 에서 자동 생성)
- 개발 단계와 현재 상태: [docs/develop_plan.md](docs/develop_plan.md)
- v1 에서 바뀐 것: [docs/migration.md](docs/migration.md)
- 셋업: [docs/setup.md](docs/setup.md)
- 사람이 할 일: [person/TODO.md](person/TODO.md)

## 실행

```bash
uv sync
cp .env.example .env          # GitHub 토큰, 감시 레포, (선택) Anthropic 키를 채운다
./scripts/run_services.sh     # 터미널 1: 결재 서버 :8790 + head_stub :8791
./scripts/run_desk.sh         # 터미널 2: 채널 멘션 → 결재함, 거절 → 재작성
```

브라우저로 http://127.0.0.1:8790/ 을 열고, 감시 레포 이슈에 `@<내 GitHub 아이디>` 로 질문을 남기면 몇 초 뒤 결재함에 초안이 뜬다. 승인하면 그 이슈에 답글이 달리고, 거절하면 사유를 반영해 다시 쓴다.
기본값은 안전 모드다 (`RFA_LLM_MODE=mock` 키 없이 규칙으로 작성, `RFA_PUBLISHER=mock` 실제로 게시하지 않음). 자세한 것은 [docs/setup.md](docs/setup.md).
