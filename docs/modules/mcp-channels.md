# 외부 채널 MCP (호스트) — mcp_channels

## 역할

외부 채널(GitHub, 이후 Confluence/L&D Hub/Slack)과의 유일한 접점. 읽기 툴은 에이전트에게 MCP로 노출하고, **쓰기(게시)는 MCP로 노출하지 않는다.** 게시는 review 서비스가 승인 뒤 Python 내부 함수로만 호출한다.

## 툴 표

### github (`services/mcp_channels/github.py`)

| 이름 | 노출 | 종류 | 결재 | 설명 |
|---|---|---|---|---|
| `list_mentions(since?)` | MCP | 읽기 | 불필요 | 감시 레포(`RFA_GITHUB_REPOS`)의 `GET /repos/{r}/issues`, `/issues/comments`(since 이후)에서 `@RFA_GITHUB_LOGIN`을 찾음. 본인이 쓴 글·since 이전 글 제외, 대소문자 무시. 한 번 돌려준 멘션은 `data/state/mentions_seen.json`에 기록해 다시 안 줌(at-most-once). `since` 생략 시 마지막 확인 시각, 처음이면 24시간 전 |
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

- 호스트 `.env`의 `GITHUB_TOKEN`: fine-grained, 감시 레포 한정, Issues read/write. **notifications API는 fine-grained 토큰을 지원하지 않아서** 멘션은 레포 이슈/댓글을 직접 읽어 찾는다. 감시 범위가 지정 레포로 좁아지는 장점도 있다.
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
├─ server.py        # MCPServer(/github/mcp) + BearerAuth + Host 허용 목록. uvicorn --factory mcp_channels.server:create_app
├─ github.py        # GithubClient(읽기 + create_comment), find_mentions, MentionTracker
└─ models.py        # Thread, ThreadComment (Mention 은 rfa_common)
services/review/publisher.py  # GithubPublisher: clearance 검증 → create_comment. RFA_PUBLISHER=github 로 선택
```

## 다른 모듈과의 연결

| 상대 | 방향 |
|---|---|
| public-desk (샌드박스) | 들어옴: list_mentions, get_thread |
| review 서비스 publisher | 들어옴(Python): post_comment |
| GitHub API | 나감 |

## 할 일

- [x] list_mentions (레포 이슈·댓글 검색, `mentions_seen.json`으로 중복 방지)
- [x] get_thread
- [x] post_comment(`create_comment`, MCP 미노출) + GithubPublisher의 clearance 검증 + 테스트
- [x] bearer 검사, streamable-http(`mcp` 2.x `MCPServer`, stateless + JSON 응답), Host 허용 목록 `RFA_MCP_ALLOWED_HOSTS`
- [x] 테스트 레포 `zetwhite/RFA_test`에서 실제 확인: 멘션 감지 → 스레드 읽기 → 승인 → 댓글 게시

## 미정

- 레포당 최근 100건(한 페이지)만 본다. 멘션이 많은 레포면 페이지네이션 필요.
- at-most-once라서 list 후 처리 중 죽으면 그 멘션은 다시 오지 않는다. 필요하면 review 쪽에서 source_url 중복 확인 방식으로 바꾼다.
- `since` 저장 위치를 review 서비스로 옮길지.
