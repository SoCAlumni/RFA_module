# 사람이 해야 하는 일

> 코드로 대신할 수 없는, 계정·토큰·외부 서비스 설정. 끝나면 체크하고 알려주세요.

## 1. Slack 앱 — 완료

절차 전체는 [docs/tokens.md](../docs/tokens.md)의 Slack 절로 옮겼습니다 (팀원 재현용, 매니페스트 방식 포함).
내 진행 상태:

- [x] 워크스페이스 확보, 앱 생성(`rfa-desk`), User Token Scopes 6개
- [x] Socket Mode + App-Level Token, user 이벤트 3종 구독
- [x] `.env`에 `SLACK_USER_TOKEN`(xoxp-), `SLACK_APP_TOKEN`(xapp-)
- [x] 테스트 채널 `#rfa-test`
- [x] 다른 계정 확보 (김아무개) — **셀프 멘션은 동작하지 않음** (비서가 내 메시지를 전부 무시), 다른 계정 필수
- [x] Slack E2E: 다른 계정 멘션 → 결재 → 내 이름으로 스레드 답글 (2026-09-27)

## 1.5. (선택) GitHub 알림 모드 쓰려면 — classic PAT

`RFA_GITHUB_MODE=notifications` 로 바꾸면 레포 지정 없이 내게 온 멘션·댓글·리뷰 요청을 다 받는다.
그러려면 **classic PAT** 가 필요하다 (지금 쓰는 fine-grained 는 알림 API 미지원):

- [ ] [docs/tokens.md](../docs/tokens.md) §2-2 대로 classic PAT 발급 (scopes `notifications`+`public_repo`) → `.env` 의 `GITHUB_TOKEN` 교체, `RFA_GITHUB_MODE=notifications`
- 알림은 자기 행동엔 안 오므로, 남이 보낸 경로 데모에는 다른 계정/팀원 멘션이 필요 (셀프 멘션은 `RFA_GITHUB_REPOS` 레포에서 계속 동작)
- 안 바꾸면 지금 그대로(mentions 모드) 계속 동작한다

## 2. 팀에 확인할 것

- [x] ~~대응 에이전트가 하나인지 채널마다 하나인지~~ → **하나**로 구현 완료 (bird-eye view 채택, [docs/plan.md](../docs/plan.md) 확정 결정 표)
- [x] ~~검열 에이전트 인터페이스~~ → head agent(`POST /ask`) 안에서 검열까지 끝내는 것으로 확정, 계약 공유됨 ([docs/contracts.md](../docs/contracts.md) §1)
- [x] ~~Frontend API~~ → 승인/거절로 확정, API 공유됨 ([docs/contracts.md](../docs/contracts.md) §2. "공개 범위 3단계"는 열린 질문으로 기재)
- [ ] **마감** — 온라인 예선 9/28 23:59 기준인지, 본선 10/7 기준인지
- [ ] **공유하기**: 팀원들에게 레포 링크 + [BOOTSTRAP.md](../BOOTSTRAP.md) 안내 (각자 AI에게 "BOOTSTRAP.md부터 읽어" 한 줄이면 됨)

## 3. 이미 되어 있는 것 (다시 안 해도 됨)

- GitHub PAT, 감시 레포 (`.env`에 설정됨)
- Slack 토큰 2개 (`.env`에 설정됨)
- uv 워크스페이스, ruff, pytest, CI(Redoc 배포)
