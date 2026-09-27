"""대응 에이전트 그래프. 멘션 하나를 결재 대기(pending)까지 데려간다.

    ask_head → write → submit → END
      │          │        │
      └──────────┴────────┴── 오류(ServiceError, LLMError) → END (failed)

- ask_head : head agent 에 질문을 넘기고 검열된 지식을 받는다.
             거절 이력이 있으면 feedback 으로 실어 보낸다.
- write    : LLM 이 채널 말투로 답을 쓴다. LLM 이 판단하는 곳은 여기뿐이다.
- submit   : 결재 서버에 올린다. 처음이면 새 안건, 거절된 안건을 다시 쓰는 중이면 revise.

흐름은 고정이다. 게시 경로는 없다 (게시는 사람이 승인한 뒤 결재 서버가 한다).
실행마다 새 상태로 시작하므로 앞 요청의 지식이 다음 요청에 섞이지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from rfa_common.contracts import (
    Approval,
    ApprovalStatus,
    AskRequest,
    AskResponse,
    CreateApprovalRequest,
    Mention,
    Rejection,
    ReviseApprovalRequest,
)

from rfa_workflow.clients import ServiceError
from rfa_workflow.deps import Deps
from rfa_workflow.llm import LLMError, prompt

AUDIENCE_LABEL = {"public": "공개 — 누구나 읽음", "company": "사내 — 동료만 읽음"}


class State(TypedDict, total=False):
    mention: Mention
    rejections: list[Rejection]  # 이 안건에서 사람이 거절한 초안들 (첫 실행은 [])
    approval_id: int | None  # 거절된 안건을 다시 쓰는 중이면 그 id
    ask: AskResponse
    draft: str
    approval: Approval
    error: str


class RunResult(BaseModel):
    outcome: Literal["pending", "failed"]
    approval_id: int | None = None
    round: int | None = None
    summary: str


# ---- 노드 ----------------------------------------------------------------------


def ask_head(state: State, deps: Deps) -> dict:
    m = state["mention"]
    req = AskRequest(
        question=m.text,
        channel=m.channel,
        audience=m.audience,
        target=m.target,
        url=m.url,
        requester=m.author,
        context=m.context,
        feedback=state["rejections"],
    )
    return {"ask": deps.head.ask(req)}


def writer_input(state: State) -> str:
    """writer 에게 줄 user 프롬프트.

    외부 사람이 쓴 글([질문], [스레드 맥락])과 사내 지식을 구획으로 나눠
    모델이 둘을 섞지 않게 한다.
    """
    m, ask = state["mention"], state["ask"]
    parts = [
        f"[채널]\n{m.channel} ({AUDIENCE_LABEL[m.audience]})",
        f"[질문한 사람]\n{m.author}",
        f"[질문]\n{m.text}",
    ]
    if m.context:
        parts.append("[스레드 맥락]\n" + "\n".join(f"- {c.author}: {c.text}" for c in m.context))
    if ask.refusal:
        parts.append(f"[답할 수 없음]\n{ask.refusal}")
    if ask.knowledge:
        parts.append(f"[head agent 가 준 지식]\n{ask.knowledge}")
    if state["rejections"]:
        parts.append(
            "[거절 이력]\n"
            + "\n".join(
                f"{i}. 거절된 초안: {r.draft}\n   거절 사유: {r.reason}"
                for i, r in enumerate(state["rejections"], 1)
            )
        )
    return "\n\n".join(parts)


def write(state: State, deps: Deps) -> dict:
    channel = state["mention"].channel
    draft = deps.llm.text(
        name="writer",
        system=prompt("writer") + "\n\n" + prompt(f"style_{channel}"),
        user=writer_input(state),
        context={"ask": state["ask"]},
    )
    return {"draft": draft}


def submit(state: State, deps: Deps) -> dict:
    ask = state["ask"]
    content = {
        "task": ask.task,
        "knowledge": ask.knowledge,
        "refusal": ask.refusal,
        "draft": state["draft"],
    }
    if state.get("approval_id") is not None:
        approval = deps.approvals.revise(state["approval_id"], ReviseApprovalRequest(**content))
    else:
        m = state["mention"]
        approval = deps.approvals.create(
            CreateApprovalRequest(
                channel=m.channel,
                audience=m.audience,
                target=m.target,
                source_url=m.url,
                requester=m.author,
                question=m.text,
                context=m.context,
                **content,
            )
        )
    return {"approval": approval}


# ---- 그래프 --------------------------------------------------------------------

Node = Callable[[State, Deps], dict]
EXPECTED = (ServiceError, LLMError)


def _guarded(node: Node, deps: Deps) -> Callable[[State], dict]:
    def run(state: State) -> dict:
        try:
            return node(state, deps)
        except EXPECTED as exc:
            return {"error": f"{node.__name__}: {exc}"}

    return run


def _next(target: str) -> Callable[[State], str]:
    def route(state: State) -> str:
        return END if state.get("error") else target

    return route


def build_graph(deps: Deps):
    g = StateGraph(State)
    for node in (ask_head, write, submit):
        g.add_node(node.__name__, _guarded(node, deps))
    g.add_edge(START, "ask_head")
    g.add_conditional_edges("ask_head", _next("write"), ["write", END])
    g.add_conditional_edges("write", _next("submit"), ["submit", END])
    g.add_edge("submit", END)
    return g.compile()


def run(
    mention: Mention,
    deps: Deps,
    rejections: list[Rejection] | None = None,
    approval_id: int | None = None,
) -> RunResult:
    """멘션 하나를 처리한다. approval_id 와 rejections 를 주면 그 안건을 다시 쓴다."""
    final: State = build_graph(deps).invoke(
        {"mention": mention, "rejections": rejections or [], "approval_id": approval_id}
    )
    if final.get("error"):
        return RunResult(outcome="failed", approval_id=approval_id, summary=final["error"])
    approval = final["approval"]
    return RunResult(
        outcome="pending",
        approval_id=approval.id,
        round=approval.round,
        summary=f"결재 대기: 안건 #{approval.id} ({approval.round}번째 초안)",
    )


def mention_of(approval: Approval) -> Mention:
    """안건에 저장된 원래 멘션을 되살린다.

    멘션 시각은 안건에 없으므로 created_at 은 안건 생성 시각으로 채운다 (그래프는 쓰지 않는다).
    """
    return Mention(
        channel=approval.channel,
        target=approval.target,
        author=approval.requester,
        text=approval.question,
        url=approval.source_url,
        created_at=approval.created_at,
        context=approval.context,
    )


def redo(approval: Approval, deps: Deps) -> RunResult:
    """사람이 거절한 안건을 사유를 반영해 다시 쓴다 (rejected → pending).

    rejected 가 아니면 head·LLM 을 부르기 전에 멈춘다 (어차피 revise 가 409 로 거부된다).
    """
    if approval.status != ApprovalStatus.REJECTED:
        return RunResult(
            outcome="failed",
            approval_id=approval.id,
            summary=f"안건 #{approval.id} 은(는) {approval.status} 상태라 다시 쓰지 않음",
        )
    return run(mention_of(approval), deps, approval.rejections, approval.id)
