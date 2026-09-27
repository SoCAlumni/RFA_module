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

실행법은 Step 6 완료 후 이 문서에 추가한다.
