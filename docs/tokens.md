# 토큰·비밀값 가이드 — 어디서 어떻게 만드나

> **전제**: 토큰 발급은 세 서비스(GitHub, Anthropic, Slack) 모두 로그인 뒤 웹 UI라 **사람만 할 수 있다.**
> 이 문서는 AI 어시스턴트가 읽고 ① `[사람]` 표시 단계는 사람에게 클릭을 정확히 불러주고 ② `[AI]` 표시 단계는 자기가 실행하도록 쓰였다.
> 검증 명령은 토큰 값을 화면에 출력하지 않는다. **토큰 값을 채팅·이슈·커밋에 붙여넣지 마라 — AI에게도.**

## 0. 먼저: 뭘 하려면 뭐가 필요한가

| 하려는 것 | 필요한 키 | 없으면 |
|---|---|---|
| 테스트·개발·API 구경 | **없음** | — |
| mock 데모 (전체 흐름, 게시는 기록만) | **없음** | — |
| 실제 Claude가 초안 작성 | `ANTHROPIC_API_KEY` + `RFA_LLM_MODE=anthropic` | mock 규칙이 초안 작성 |
| GitHub 실연동 (멘션 수신·댓글 게시) | `GITHUB_TOKEN`, `RFA_GITHUB_LOGIN`, `RFA_GITHUB_REPOS` + `RFA_CHANNELS`에 `github` | github 채널이 시작 시 오류 |
| Slack 실연동 (DM·멘션 수신·답글) | `SLACK_USER_TOKEN`, `SLACK_APP_TOKEN` + `RFA_CHANNELS`에 `slack` | slack 채널이 시작 시 오류 |
| 실제 채널에 게시까지 | 위 채널 키 + `RFA_PUBLISHER=live` | `mock`이면 게시 기록만 |

**mock이면 토큰이 0개다.** 통합 개발(head agent, 프런트)에는 토큰이 필요 없다 — 실연동 데모를 할 때만 아래로 내려가라.

## 1. `.env` 만들기

```bash
cp .env.example .env && chmod 600 .env
```

`.env`는 gitignore라 커밋되지 않는다. 키를 채운 뒤의 각 절 끝 `[AI]` 검증 명령은 값 노출 없이 유효성만 확인한다.

## 2. GitHub — fine-grained PAT

준비물: 감시할 테스트 레포. `[사람]` 본인 계정에 public 레포 하나 만들기(Issues 켜짐이 기본). 팀 공용 `SoCAlumni/RFA_test`를 쓰려면 관리자에게 협업자 초대를 요청해도 된다.

`[사람]` 발급 (클릭 순서):
1. github.com 우상단 프로필 → **Settings**
2. 왼쪽 맨 아래 **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**
3. 이름 아무거나, **Expiration** 30일 정도
4. **Repository access** → **Only select repositories** → 테스트 레포만 선택
5. **Permissions** → **Repository permissions** → **Issues** → **Read and write**
6. **Generate token** → `github_pat_...` 복사

`[사람]` `.env`에 채우기:
```
GITHUB_TOKEN=github_pat_...
RFA_GITHUB_LOGIN=내_github_아이디      # 이 아이디를 @멘션하면 비서가 받는다
RFA_GITHUB_REPOS=owner/테스트레포
```

`[AI]` 검증 (값 노출 없음):
```bash
uv run --env-file .env python -c "
import os
from datetime import UTC, datetime
from pathlib import Path
from channels.github import GithubChannel
ch = GithubChannel.from_env(os.environ, Path('/tmp'))
ch.client.issues_since(ch.config.repos[0], datetime.now(UTC))
print('GitHub 토큰·레포 OK:', ch.config.repos)"
```
`OK`가 나오면 유효하다. 오류에 401이 보이면 토큰, 404면 레포 이름을 다시 봐라.

주의: **classic PAT이 아니라 fine-grained**다. (지름길: `gh` CLI에 이미 로그인돼 있다면 `GITHUB_TOKEN="$(gh auth token)"`도 동작하지만, 계정 전체 레포 권한이라 데모 임시용으로만.)

## 3. Anthropic — Claude API 키

`[사람]`:
1. https://console.anthropic.com → **API Keys** → **Create Key** → 복사 (크레딧이 있어야 호출된다)
2. `.env`: `ANTHROPIC_API_KEY=...`, `RFA_LLM_MODE=anthropic` (모델은 `RFA_MODEL=claude-sonnet-4-6`, 팀 결정)

`[AI]` 검증 — 서비스 켠 뒤 멘션 하나를 돌려 본다 (실 호출 한 번, 몇 센트):
```bash
./scripts/run_services.sh   # 다른 터미널
uv run --env-file .env python -m rfa_workflow run --mention-json '{"channel":"slack","target":"C0123ABC/1727000000.000100","author":"t","text":"ORBIT 벤치마크 진행 어때요?","url":"https://slack.com/archives/C0123ABC/p1727000000000100","created_at":"2026-09-27T10:00:00Z"}'
```
`"outcome":"pending"`이면 성공. 401·credit 오류는 요약(summary)에 그대로 찍힌다.

## 4. Slack — User Token 방식 (봇 아님)

**왜 봇이 아닌가**: 이 비서는 별도 봇 계정이 아니라 **나로서** 동작한다 — 나에게 온 DM과 `@나` 멘션을 받고, 답글도 내 이름으로 단다. GitHub 채널(내 PAT)과 같은 구조다. 그래서 모든 권한·이벤트를 **User 쪽**에 설정한다 (Bot 쪽에 넣으면 동작하지 않는다).

⚠️ 이 토큰은 **내 DM 전체와 내가 속한 채널 전체**를 읽을 수 있다. 반드시 개인/데모 워크스페이스에서만 써라.

### 4-1. 워크스페이스

`[사람]` 데모용 워크스페이스 확보 — 없으면 https://slack.com/get-started 에서 무료 생성(3분). **회사 워크스페이스는 피해라** (앱 설치에 관리자 승인 필요). 팀 데모 워크스페이스에 초대받아도 되지만, 토큰은 어차피 아래 절차를 **자기 계정으로** 다시 해야 한다 (User Token은 계정별이다).

### 4-2. 앱 만들기 — 매니페스트로 (권장, 2분)

`[사람]`:
1. https://api.slack.com/apps → **Create New App** → **From an app manifest**
2. 워크스페이스 선택 → YAML 탭에 레포 루트의 [`slack-app-manifest.yaml`](../slack-app-manifest.yaml) 내용 붙여넣기 → **Create**
   (권한 6개, user 이벤트 3종, Socket Mode가 한 번에 설정된다)
3. **Install App**(왼쪽 메뉴) → **Install to Workspace** → 허용 → **User OAuth Token** 복사 — **`xoxp-`로 시작** (`xoxb-` 봇 토큰이 아니다)
4. **Basic Information** → **App-Level Tokens** → **Generate Token and Scopes** → 이름 아무거나, scope **`connections:write`** 추가 → Generate → `xapp-...` 복사
   (Socket Mode 연결용 토큰. 매니페스트로는 만들 수 없어 이것만 클릭이 남는다)

<details><summary>매니페스트가 안 될 때 — 수동 설정 (From scratch)</summary>

1. Create New App → **From scratch** → 이름 `rfa-desk`, 워크스페이스 선택
2. **OAuth & Permissions** → **User Token Scopes**(Bot 아님)에 추가: `channels:history`, `groups:history`, `im:history`, `mpim:history`, `chat:write`, `users:read`
3. **Socket Mode** → Enable (여기서 App-Level Token 생성, scope `connections:write` → `xapp-`)
4. **Event Subscriptions** → Enable → **Subscribe to events on behalf of users**(bot events 아님)에 `message.im`, `message.channels`, `message.groups` → Save (재설치하라면 재설치)
5. **Install to Workspace** → `xoxp-` User OAuth Token 복사
</details>

### 4-3. `.env`와 검증

`[사람]` `.env`에:
```
SLACK_USER_TOKEN=xoxp-...
SLACK_APP_TOKEN=xapp-...
```

`[AI]` 검증: `.env`의 `RFA_CHANNELS`에 `slack`을 넣고(`github,slack` 또는 `slack`) desk를 한 틱 —
```bash
./scripts/run_desk.sh --once
```
로그에 `slack: U... 로 이벤트 받는 중`이 나오면 연결 성공. `invalid_auth`면 토큰, `missing_scope`면 권한 설정을 다시 봐라.

### 4-4. 테스트 준비 — **다른 계정이 필수다**

- `[사람]` 테스트 채널(예: `#rfa-test`)을 만들고 내가 들어가 있는지 확인. 봇 초대는 필요 없다 — 내가 있는 채널이면 된다.
- `[사람]` **나에게 멘션·DM을 보내 줄 다른 계정**을 확보 (팀원 초대 또는 다른 이메일로 두 번째 계정).
  비서는 **내가 보낸 메시지를 전부 무시**한다 (비서 답글도 내 이름으로 달리므로 이 규칙이 자기 답글 루프를 막는다). 그래서 **셀프 멘션·셀프 DM은 동작하지 않는다.**
- 멘션은 `@`를 치고 **자동완성에서 사람을 골라야** 한다 (글자로만 치면 멘션이 아니다). 질문의 단어는 띄어 써라 — `ORBIT벤치마크`처럼 붙이면 지식 대역(head_stub)이 업무를 못 찾는다.

## 5. 팀 공용 자원으로 재현하려면

- GitHub: 관리자가 테스트 레포 **Settings → Collaborators**에서 초대 → 본인 fine-grained PAT의 Repository access에 그 레포 선택.
- Slack: 워크스페이스에 초대받은 뒤 **4-2~4-3을 자기 계정으로** 수행 (남의 `xoxp-` 토큰을 받아 쓰지 마라 — 그 사람의 DM 전체가 읽힌다).
