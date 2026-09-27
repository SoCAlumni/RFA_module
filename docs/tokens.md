# 토큰·비밀값 가이드 — 어디서 어떻게 만드나

> **전제**: 토큰 발급은 어느 서비스(GitHub, LLM provider, Slack)든 로그인 뒤 웹 UI라 **사람만 할 수 있다.**
> 이 문서는 AI 어시스턴트가 읽고 ① `[사람]` 표시 단계는 사람에게 클릭을 정확히 불러주고 ② `[AI]` 표시 단계는 자기가 실행하도록 쓰였다.
> 검증 명령은 토큰 값을 화면에 출력하지 않는다. **토큰 값을 채팅·이슈·커밋에 붙여넣지 마라 — AI에게도.**

## 0. 먼저: 뭘 하려면 뭐가 필요한가

| 하려는 것 | 필요한 키 | 없으면 |
|---|---|---|
| 테스트·개발·API 구경 | **없음** | — |
| mock 데모 (전체 흐름, 게시는 기록만) | **없음** | — |
| 실제 LLM이 초안 작성 (무료) | `OPENROUTER_API_KEY` + `RFA_LLM_MODE=openrouter` (기본) | mock 규칙이 초안 작성 |
| 다른 LLM provider 로 전환 | `NVIDIA_API_KEY` \| `GEMINI_API_KEY` \| `ANTHROPIC_API_KEY` + `RFA_LLM_MODE=nvidia\|gemini\|anthropic` | — |
| GitHub 실연동 (멘션 수신·댓글 게시) | `GITHUB_TOKEN`, `RFA_GITHUB_LOGIN`, `RFA_GITHUB_REPOS` + `RFA_CHANNELS`에 `github` | github 채널이 시작 시 오류 |
| Slack 실연동 (DM·멘션 수신·답글) | `SLACK_USER_TOKEN`, `SLACK_APP_TOKEN` + `RFA_CHANNELS`에 `slack` | slack 채널이 시작 시 오류 |
| 실제 채널에 게시까지 | 위 채널 키 + `RFA_PUBLISHER=live` | `mock`이면 게시 기록만 |

**mock이면 토큰이 0개다.** 통합 개발(head agent, 프런트)에는 토큰이 필요 없다 — 실연동 데모를 할 때만 아래로 내려가라.

## 1. `.env` 만들기

```bash
cp .env.example .env && chmod 600 .env
```

`.env`는 gitignore라 커밋되지 않는다. 키를 채운 뒤의 각 절 끝 `[AI]` 검증 명령은 값 노출 없이 유효성만 확인한다.

## 2. GitHub — 받기 모드에 따라 토큰이 다르다

`RFA_GITHUB_MODE` 로 고른다:

| 모드 | 무엇을 받나 | 토큰 |
|---|---|---|
| `mentions` (기본) | `RFA_GITHUB_REPOS` 레포에서 `@나` 가 든 글 (남·나 모두 — 혼자 데모 가능) | **fine-grained PAT** (레포 한정, 최소 권한) |
| `notifications` | **내 알림함** — 남이 보낸 멘션, 내가 연 이슈/PR 의 댓글, 담당 지정, 리뷰 요청을 레포 무관하게. 셀프 멘션은 `RFA_GITHUB_REPOS` 레포에서 스캔으로 보탬 | **classic PAT** — 알림 API 는 "personal access token (classic)만 지원"(공식 문서). 권한 `notifications` + `public_repo` (비공개 레포는 `repo`) |

공통 준비물: 테스트 레포. `[사람]` 본인 계정에 public 레포 하나 만들기(Issues 켜짐이 기본). 팀 공용 `SoCAlumni/RFA_test`를 쓰려면 관리자에게 협업자 초대를 요청해도 된다.

### 2-1. mentions 모드 — fine-grained PAT

`[사람]` 발급 (클릭 순서):
1. github.com 우상단 프로필 → **Settings**
2. 왼쪽 맨 아래 **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**
3. 이름 아무거나, **Expiration** 30일 정도
4. **Repository access** → **Only select repositories** → 테스트 레포만 선택
5. **Permissions** → **Repository permissions** → **Issues** → **Read and write**
6. **Generate token** → `github_pat_...` 복사

`[사람]` `.env`에 채우기:
```
RFA_GITHUB_MODE=mentions
GITHUB_TOKEN=github_pat_...
RFA_GITHUB_REPOS=owner/테스트레포      # 이 레포에서 @나 를 찾는다 (필수)
RFA_GITHUB_LOGIN=                     # 선택 — 비우면 토큰으로 자동 감지
```

### 2-2. notifications 모드 — classic PAT

`[사람]` 발급 (클릭 순서):
1. github.com → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**
2. 이름 아무거나, **Expiration** 30일 정도
3. **Select scopes** 에서 체크: **`notifications`** + **`public_repo`** (비공개 레포도 쓰면 `repo` 전체)
4. **Generate token** → `ghp_...` 복사

`[사람]` `.env`에 채우기:
```
RFA_GITHUB_MODE=notifications
GITHUB_TOKEN=ghp_...
RFA_GITHUB_REPOS=owner/테스트레포      # 셀프 멘션(내가 쓴 @나)을 받을 레포. 비우면 알림만
```

주의: 알림은 **자기 행동에는 오지 않는다** — 남이 보낸 것은 알림으로, 내가 나를 멘션한 것은 위 레포 스캔으로 받는다. 폴링 주기는 GitHub 이 정한다(X-Poll-Interval, 보통 60초) — 멘션 후 안건까지 최대 1분쯤 걸릴 수 있다.

### 2-3. `[AI]` 검증 (두 모드 공통, 값 노출 없음)

```bash
uv run --env-file .env python -c "
import os
from pathlib import Path
from channels.github import GithubChannel
ch = GithubChannel.from_env(os.environ, Path('/tmp'))
ch.start()
print('GitHub OK — 모드:', ch.config.mode, '| 계정:', ch.login, '| 레포:', ch.config.repos)"
```
401 이면 토큰이 틀린 것. notifications 모드에서 **403 "Resource not accessible by personal access token"** 이면 십중팔구 fine-grained 토큰을 그대로 쓰고 있는 것이다 — classic 으로 재발급 (실측한 오류 문구다).

(지름길: `gh` CLI 에 이미 로그인돼 있다면 `GITHUB_TOKEN="$(gh auth token)"` 은 classic 계열이라 notifications 모드에서도 대체로 동작 — 계정 전체 권한이라 데모 임시용으로만.)

## 3. LLM provider 키 — 초안을 실제 모델이 쓰게

`RFA_LLM_MODE` 로 provider 를 고른다. 모델은 provider 별 기본값이 있어 `RFA_MODEL` 은 비워 둬도 된다.

**OpenRouter (기본, 무료 nemotron)** `[사람]`:
1. https://openrouter.ai 가입 → https://openrouter.ai/keys → **Create Key** → 복사 (무료, 카드 불필요)
2. `.env`: `OPENROUTER_API_KEY=...`, `RFA_LLM_MODE=openrouter` (기본 모델 `nvidia/nemotron-3.5-lightning:free`, 무료라 요청 수 제한 있음)

**NVIDIA 공식 (nemotron)** `[사람]`:
1. https://build.nvidia.com 에서 NVIDIA 계정 로그인 → 모델 페이지에서 **Get API Key** → 복사 (무료 크레딧)
2. `.env`: `NVIDIA_API_KEY=...`, `RFA_LLM_MODE=nvidia` (기본 모델 `nvidia/nemotron-3.5-lightning-30b-a3b`)

**Gemini** `[사람]`:
1. https://aistudio.google.com/apikey → **Create API key** → 복사
2. `.env`: `GEMINI_API_KEY=...`, `RFA_LLM_MODE=gemini` (기본 모델 `gemini-2.5-flash`)

**Anthropic (Claude)** `[사람]`:
1. https://console.anthropic.com → **API Keys** → **Create Key** → 복사 (크레딧이 있어야 호출된다)
2. `.env`: `ANTHROPIC_API_KEY=...`, `RFA_LLM_MODE=anthropic` (기본 모델 `claude-sonnet-4-6`)

`[AI]` 검증 — 서비스 켠 뒤 멘션 하나를 돌려 본다 (실 호출 한 번, openrouter/nvidia 는 무료):
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
