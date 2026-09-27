# 사람이 해야 하는 일

> 코드로 대신할 수 없는, 계정·토큰·외부 서비스 설정. 끝나면 체크하고 알려주세요.

## 1. Slack 앱 만들기 (10분) — 지금 막힌 유일한 블로커

GitHub 봇에 PAT이 필요하듯, Slack에서 프로그램이 메시지를 읽고 쓰려면
워크스페이스에 "앱"을 등록해야 합니다. 그 앱이 봇 계정이 되고 토큰을 줍니다.

### 1-1. 워크스페이스

- [ ] 데모용 Slack 워크스페이스 확보
  - 이미 쓰는 개인 워크스페이스가 있으면 그걸로
  - 없으면 https://slack.com/get-started 에서 무료로 새로 생성 (3분)
  - **회사 워크스페이스는 피하세요.** 앱 설치에 관리자 승인이 필요해서 오늘 저녁에 막힐 수 있습니다

### 1-2. 앱 생성

- [ ] https://api.slack.com/apps 접속
- [ ] `Create New App` → `From scratch`
- [ ] App Name: `rfa-desk`, 워크스페이스: 위에서 정한 것

### 1-3. 권한 주기

- [ ] 왼쪽 메뉴 `OAuth & Permissions` → `Scopes` → `Bot Token Scopes` → `Add an OAuth Scope`

  | scope | 왜 필요한가 |
  |---|---|
  | `app_mentions:read` | 봇을 멘션한 메시지를 받기 |
  | `channels:history` | 공개 채널 대화 읽기 (스레드 맥락) |
  | `groups:history` | 비공개 채널 대화 읽기 |
  | `im:history` | DM 읽기 |
  | `chat:write` | 답장 보내기 |
  | `users:read` | 보낸 사람 이름 조회 |

### 1-4. 설치하고 봇 토큰 받기

- [ ] 같은 화면 위쪽 `Install to Workspace` → `허용`
- [ ] `Bot User OAuth Token` 복사 — **`xoxb-` 로 시작**

### 1-5. Socket Mode 켜고 앱 토큰 받기

공개 URL(ngrok) 없이 로컬에서 Slack 이벤트를 받기 위한 설정입니다.

- [ ] 왼쪽 메뉴 `Socket Mode` → `Enable Socket Mode` 켜기
- [ ] 토큰 이름을 물어보면 아무거나 (예: `rfa-socket`), scope는 `connections:write`
- [ ] 생성된 `App-Level Token` 복사 — **`xapp-` 로 시작**

### 1-6. 이벤트 구독

- [ ] 왼쪽 메뉴 `Event Subscriptions` → `Enable Events` 켜기
- [ ] `Subscribe to bot events` 에 추가:
  - `app_mention` (채널에서 봇을 멘션)
  - `message.im` (봇에게 온 DM)
- [ ] 저장하라고 하면 저장, 앱 재설치하라고 하면 재설치

### 1-7. 봇 초대

- [ ] Slack 앱에서 테스트할 채널을 하나 만듭니다 (예: `#rfa-test`)
- [ ] 그 채널에 `/invite @rfa-desk` 입력

### 1-8. 토큰 전달

- [ ] `.env` 에 아래 두 줄 추가 (`.env` 는 gitignore 되어 있어 커밋되지 않습니다)

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

- [ ] 추가했다고 알려주세요. 토큰 값 자체는 채팅에 붙여넣지 마세요

---

## 2. 팀에 확인할 것

- [ ] **대응 에이전트가 하나인지 채널마다 하나인지** — bird-eye view와 UI 시안이 다릅니다
      (`rfa-agent-layout.html`: "하나 · 모든 채널" / UI 시안: public-desk, collab-desk, mail-desk)
- [ ] **검열 에이전트 인터페이스** (민섭님) — 제가 훅 자리만 뚫어둘 텐데, 어떤 모양으로 부를지
- [ ] **Frontend API** (다영님) — 결재가 "승인/거절"이 아니라 "공개 범위 3단계 선택"으로 바뀐 게 맞는지
- [ ] **마감** — 온라인 예선 9/28 23:59 기준인지, 본선 10/7 기준인지

---

## 3. 이미 되어 있는 것 (다시 안 해도 됨)

- GitHub PAT, 감시 레포, MCP 토큰 (`.env` 에 설정됨)
- mkcert 인증서 (`certs/`)
- uv 워크스페이스, ruff, pytest, CI
