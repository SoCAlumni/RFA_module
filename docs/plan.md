# RFA_module 계획

> 최종 수정: 2026-09-26. 토의 결과를 정리한 문서. 세부는 `docs/architecture.md`와 `docs/modules/*.md` 참고.

## 배경

- NVIDIA Agentic AI 해커톤 온라인 사전 챌린지 제출용. 마감 2026-09-28 23:59.
- 팀 서비스: 개인 비서 멀티에이전트 (nemoclaw + OpenShell 기반, 로컬 실행).
- 채점: NVIDIA Agent 기술 활용 심도, 실용성/산업가치, 완성도, 독창성.

## 업무 분장 (reference/team_meeting.md)

| 담당 | 모듈 |
|---|---|
| 민섭 | 실무 Agent(실무대장). 사용자 노트 + 검색/추론으로 task별 지식 축적. task별 에이전트 동적 생성 |
| **승희 (이 레포)** | 외부게시 MCP Agent. 외부 요청 대응(언론사) + 기밀검토 + 외부 채널 MCP 및 tool call 정책 |
| 다영 | OpenShell 기반 에이전트별 정책/권한, 플러그인, UI |

팀 공통 스택: 에이전트 설계는 **LangGraph**, 모듈 간 인터페이스는 **MSA + OpenAPI**.

## 이 모듈이 하는 일 (한 줄)

외부 채널에서 온 요청을 받아, 실무대장에게 지식을 받고, 언론사가 초안을 쓰고, 기밀검토를 거쳐, 사람 결재 후, 외부 채널에 게시한다.

## 확정된 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 흐름 구분 | 카테고리 없음. 채널 보안 범위로만 Public / Internal | 회의록 구조 그대로 |
| 지식 출처 | 실무대장(민섭)에게 OpenAPI로 요청. 지금은 같은 계약의 stub | 외부채널 담당자는 supervisor와만 소통 |
| 초안 | 언론사(writer)가 쓰고 editor가 첨삭 (최대 2회) | debate는 허용된 예외 |
| 기밀검토 | Public → 2-B CODE 관리자, Internal → 2-A. 둘 다 official + personal 기준 + 사람 피드백 | 회의록 2-A/2-B |
| 비밀값 스캔 | 채널 무관, 호스트 서버가 자동 실행 | LLM이 놓칠 수 있는 토큰/IP는 코드로 |
| 게이트 | 하드 게이트. 서명된 승인 토큰 없으면 게시 불가 | "유출 방지"가 핵심 |
| 사람 결재 | 호스트 전용 알람/결재 웹에서만. 에이전트에게 승인·게시 기능 없음 | LLM이 승인을 대신 누르는 경로 차단 |
| 흐름 제어 | LangGraph 고정 그래프. LLM이 고르는 건 노드 안의 판단뿐 | 자유도가 높으면 탈주 |
| 입구 | OpenClaw 에이전트 `public-desk` + cron | nemoclaw always-on 활용 |
| 외부 연동 | GitHub만 실제. Confluence/L&D Hub/Slack은 인터페이스만 | 마감 |
| 언어 | Python (FastAPI, FastMCP, LangGraph) | |

## 전체 그림

```
외부 채널 (GitHub 멘션)
  │
  ▼
[샌드박스 rfa]
  public-desk (OpenClaw, cron) ── github.list_mentions ──► workflow.run(mention)
                                                            │ LangGraph (Public 대응 그래프)
                                                            │ intake → ask_knowledge → write ⇄ edit
                                                            │ → submit → censor_public → 결재 대기
[호스트]
  review 서비스 (OpenAPI): 결재 문서, 상태기계, 스캐너, 알람/결재 웹, 서명, 게시
  knowledge stub (OpenAPI): 실무대장 계약 흉내
  mcp_channels (FastMCP): github
  사람 ── 결재 웹에서 승인 ──► 서명 토큰 ──► GitHub 게시
```

## 일정

| 날짜 | 목표 |
|---|---|
| 9/26 | 문서 → 호스트 서비스 → LangGraph Public 그래프 → 샌드박스 재현 → GitHub E2E 1회 |
| 9/27 | Internal 대응(1-C) + 2-A censor. 채널은 로컬 파일로 흉내 |
| 9/28 | 개인 기밀 기준 관리 UI/API, README, 제출 자료 |

## GitHub 공유 원칙

- 올리는 것: 코드, `agents/agents.yaml`, 정책 파일, 스크립트, stub 데이터, 문서.
- 올리지 않는 것: 샌드박스/이미지, 대화 기록, `data/state/`, **비밀값**(`.env`).
- 팀원은 `scripts/setup_sandbox.sh`로 샌드박스를 재현한다. `nemoclaw config export`는 현재 환경에서 실패하므로 의존하지 않는다.

## 리스크

| 리스크 | 대응 |
|---|---|
| 샌드박스 → 호스트 서비스 네트워크 연결 (`mcp add`는 HTTPS + 비-loopback 사설 호스트만) | docker bridge IP + mkcert. 막히면 LangGraph를 호스트에서 돌려 데모 확보 후 이전 |
| `workflow.run`을 OpenClaw 툴로 노출하는 방식 | stdio MCP 우선, 안 되면 exec 스킬 |
| 실무대장 계약이 민섭님 구현과 어긋남 | 계약을 일요일 미팅 전에 공유, stub은 계약만 지킴 |
