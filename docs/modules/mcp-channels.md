# 외부 채널 MCP (호스트) — mcp_channels

## 역할

외부 채널(GitHub, 이후 Confluence/L&D Hub/Slack)과의 유일한 접점. 읽기 툴은 에이전트에게 MCP로 노출하고, **쓰기(게시)는 MCP로 노출하지 않는다.** 게시는 review 서비스가 승인 뒤 Python 내부 함수로만 호출한다.

## 툴 표

### github (`services/mcp_channels/github.py`)

| 이름 | 노출 | 종류 | 결재 | 설명 |
|---|---|---|---|---|
| `list_mentions(since)` | MCP | 읽기 | 불필요 | `GET /notifications?participating=true` 중 reason=mention. `Mention{target, author, text, url, created_at}` |
| `get_thread(target)` | MCP | 읽기 | 불필요 | 이슈/PR 본문 + 최근 댓글 N개 |
| `get_diff(pr)` | MCP | 읽기 | 불필요 | 9/28 코드리뷰 대비, 오늘은 미구현 |
| `post_comment(target, body, clearance)` | **내부 함수만** | 쓰기 | **필수** | `clearance.verify` 통과 시 `POST /repos/{o}/{r}/issues/{n}/comments` |

`target` 형식: `owner/repo#number`.

### 이후 채널 (인터페이스만)

| 채널 | 보안 범위 | 읽기 | 쓰기(결재 후) |
|---|---|---|---|
| confluence (SR 사업부) | internal | `list_requests`, `get_page` | `create_page`, `add_comment` |
| ldhub (회사 블로그) | internal | — | `publish_post` |
| slack | internal/public | `list_mentions`, `get_thread` | `post_message` |

모두 같은 규칙: 읽기는 MCP, 쓰기는 내부 함수 + clearance.

## 토큰 취급

- 호스트 `.env`의 `GITHUB_TOKEN`(git 없이 fine-grained: issues read/write, notifications read).
- nemoclaw 등록 시 `--env GITHUB_MCP_TOKEN`은 **MCP 서버 자체 인증**(bearer)용이고 GitHub 토큰이 아니다. 샌드박스에는 어느 토큰도 들어가지 않는다.
- 게시에 쓰는 GitHub 토큰은 호스트 프로세스에만 있다.

## nemoclaw 등록

```bash
# scripts/register_mcp.sh
export GITHUB_MCP_TOKEN="$(openssl rand -hex 24)"     # MCP 서버 bearer, .env에도 저장
nemoclaw rfa mcp add github \
  --url https://rfa-host.local/github/mcp \
  --env GITHUB_MCP_TOKEN \
  --trusted-private-host rfa-host.local \
  --deny-tool 'post_*' --deny-tool 'admin_*'
```
- `mcp add`는 HTTPS + 비-loopback 사설 호스트만 받는다. `rfa-host.local`은 docker bridge IP(예: 172.17.0.1)로 `hosts-add`, 인증서는 mkcert, CA는 `NEMOCLAW_CORPORATE_CA_BUNDLE`.
- `--deny-tool 'post_*'`는 이중 안전장치. 애초에 post는 MCP에 없다.

## 파일 구조

```
services/mcp_channels/
├─ server.py        # FastMCP 앱 마운트 (/github/mcp), bearer 검사
├─ github.py        # 툴 + post_comment 내부 함수
└─ models.py        # Mention, Thread
```

## 다른 모듈과의 연결

| 상대 | 방향 |
|---|---|
| public-desk (샌드박스) | 들어옴: list_mentions, get_thread |
| review 서비스 publisher | 들어옴(Python): post_comment |
| GitHub API | 나감 |

## 할 일

- [ ] Mention 모델, list_mentions (since 기준 중복 방지: `data/state/mentions_seen.json`)
- [ ] get_thread
- [ ] post_comment + clearance.verify + 테스트(무토큰/위조/본문 변경 거부)
- [ ] bearer 검사, FastMCP streamable-http 마운트
- [ ] 테스트 레포 `zetwhite/rfa-test` 준비, 이슈 하나 생성

## 미정

- notifications API가 멘션을 늦게 주는 경우 대비해 issue comments 검색(`search/issues?q=mentions:zetwhite`) 병행 여부.
- `since` 저장 위치를 review 서비스로 옮길지.
