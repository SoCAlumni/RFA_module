# 사람이 해야 하는 일

> 코드로 대신할 수 없는, 계정·토큰·외부 서비스 설정. 끝나면 체크하고 알려주세요.

## 1. Slack 앱 만들기 (10분) — 지금 막힌 유일한 블로커

GitHub 봇에 PAT이 필요하듯, Slack에서 프로그램이 메시지를 읽고 쓰려면
워크스페이스에 "앱"을 등록해야 합니다.

**우리는 봇 계정이 아니라 User Token 방식을 씁니다.** 별도 봇을 멘션하는 게 아니라,
**나에게 온 DM과 `@나` 멘션**을 비서가 받고 **내 이름으로** 답합니다 (GitHub 채널과 같은 구조).
그래서 아래 권한·이벤트는 전부 "User" 쪽에 설정합니다 — Bot 쪽에 넣으면 동작하지 않습니다.

⚠️ 이 토큰은 **내 DM 전체와 내가 속한 채널 전체**를 읽을 수 있습니다.
반드시 개인/데모 워크스페이스에서만 쓰세요.

### 1-1. 워크스페이스

- [x] 데모용 Slack 워크스페이스 확보
  - 이미 쓰는 개인 워크스페이스가 있으면 그걸로
  - 없으면 https://slack.com/get-started 에서 무료로 새로 생성 (3분)
  - **회사 워크스페이스는 피하세요.** 앱 설치에 관리자 승인이 필요해서 오늘 저녁에 막힐 수 있습니다

### 1-2. 앱 생성

- [ ] https://api.slack.com/apps 접속
- [ ] `Create New App` → `From scratch`
- [ ] App Name: `rfa-desk`, 워크스페이스: 위에서 정한 것

### 1-3. 권한 주기

- [ ] 왼쪽 메뉴 `OAuth & Permissions` → `Scopes` → **`User Token Scopes`** → `Add an OAuth Scope`
      (`Bot Token Scopes` 아님 — 그쪽은 비워 둡니다)

  | scope | 왜 필요한가 |
  |---|---|
  | `channels:history` | 내가 속한 공개 채널 대화 읽기 (멘션 감지 + 스레드 맥락) |
  | `groups:history` | 내가 속한 비공개 채널 대화 읽기 |
  | `im:history` | 나에게 온 DM 읽기 |
  | `mpim:history` | 그룹 DM 읽기 |
  | `chat:write` | 내 이름으로 답장 보내기 |
  | `users:read` | 보낸 사람 이름 조회 |

### 1-4. 설치하고 유저 토큰 받기

- [ ] 같은 화면 위쪽 `Install to Workspace` → `허용`
- [ ] `User OAuth Token` 복사 — **`xoxp-` 로 시작** (`xoxb-` 봇 토큰이 아닙니다)

### 1-5. Socket Mode 켜고 앱 토큰 받기

공개 URL(ngrok) 없이 로컬에서 Slack 이벤트를 받기 위한 설정입니다.

- [ ] 왼쪽 메뉴 `Socket Mode` → `Enable Socket Mode` 켜기
- [ ] 토큰 이름을 물어보면 아무거나 (예: `rfa-socket`), scope는 `connections:write`
- [ ] 생성된 `App-Level Token` 복사 — **`xapp-` 로 시작**

### 1-6. 이벤트 구독

- [ ] 왼쪽 메뉴 `Event Subscriptions` → `Enable Events` 켜기
- [ ] **`Subscribe to events on behalf of users`** 에 추가 (`Subscribe to bot events` 아님):
  - `message.im` (나에게 온 DM)
  - `message.channels` (내가 속한 공개 채널의 메시지 — 코드가 `@나` 멘션만 골라냄)
  - `message.groups` (내가 속한 비공개 채널의 메시지)
- [ ] 저장하라고 하면 저장, 앱 재설치하라고 하면 재설치

### 1-7. 테스트 채널과 상대 계정

봇을 초대할 필요는 없습니다 — 내가 들어가 있는 채널이면 됩니다.

- [ ] 테스트할 채널을 하나 만들고 (예: `#rfa-test`) 내가 들어가 있는지 확인
- [ ] **나에게 DM/멘션을 보내 줄 다른 계정 확보** — 팀원을 워크스페이스에 초대하거나,
      다른 이메일로 두 번째 계정 생성 (내가 나를 멘션해도 동작은 하지만 데모 그림이 안 삽니다)

### 1-8. 토큰 전달

- [ ] `.env` 에 아래 두 줄 추가 (`.env` 는 gitignore 되어 있어 커밋되지 않습니다)

```
SLACK_USER_TOKEN=xoxp-...
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
