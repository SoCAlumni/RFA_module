"""결재 서버 (웹 백엔드). contracts/approvals.openapi.yaml 구현.

실행: uvicorn --factory approvals.app:create_app --port 8790
- 프런트(다영님)가 부르는 것: GET /approvals, /approvals/summary, /approvals/{id},
  POST /approvals/{id}/approve, /approvals/{id}/reject
- desk 만 부르는 것: POST /approvals, POST /approvals/{id}/revise
- GET / : 같은 API 를 쓰는 참조 결재 웹 (브라우저 http://127.0.0.1:8790/)

env: RFA_DATA_DIR (안건 파일은 <RFA_DATA_DIR>/state/), RFA_PUBLISHER,
     RFA_CORS_ORIGINS (쉼표로 구분, 기본 *)
인증 없음. 에이전트가 승인을 부르지 못하게 막는 것은 샌드박스 정책의 몫이다.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from rfa_common.contracts import (
    Approval,
    ApprovalStatus,
    ApprovalSummary,
    ApproveRequest,
    ChannelKind,
    CreateApprovalRequest,
    Edit,
    Rejection,
    RejectRequest,
    ReviseApprovalRequest,
)

from approvals.publisher import Publisher, PublishError, make_publisher
from approvals.store import (
    MAX_ROUNDS,
    ApprovalNotFound,
    ApprovalStore,
    InvalidTransition,
    Step,
    now,
)

S = ApprovalStatus
DEFAULT_DATA_DIR = Path("./data")
INDEX_HTML = Path(__file__).parent / "static" / "index.html"


def create_app(
    data_dir: Path | None = None,
    publisher: Publisher | None = None,
    env: Mapping[str, str] | None = None,
) -> FastAPI:
    env = os.environ if env is None else env
    root = data_dir or Path(env.get("RFA_DATA_DIR", DEFAULT_DATA_DIR))
    store = ApprovalStore(root / "state")
    publish_to = publisher if publisher is not None else make_publisher(env)
    approving = threading.Lock()  # 승인 버튼 연타로 두 번 게시되지 않게

    app = FastAPI(title="RFA approvals", version="0.3.0")
    origins = [o.strip() for o in env.get("RFA_CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"]
    )

    @app.exception_handler(ApprovalNotFound)
    def _not_found(_: Request, exc: ApprovalNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})

    @app.exception_handler(InvalidTransition)
    def _conflict(_: Request, exc: InvalidTransition) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"error": "invalid_transition", "detail": str(exc)}
        )

    @app.exception_handler(PublishError)
    def _publish_failed(_: Request, exc: PublishError) -> JSONResponse:
        return JSONResponse(
            status_code=502, content={"error": "publish_failed", "detail": str(exc)}
        )

    # ---- frontend ----------------------------------------------------------

    @app.get("/approvals", response_model=list[Approval], tags=["frontend"])
    def list_approvals(
        status: ApprovalStatus | None = None,
        channel: ChannelKind | None = None,
        task: str | None = None,
    ) -> list[Approval]:
        return store.list(status, channel, task)

    @app.get("/approvals/summary", response_model=ApprovalSummary, tags=["frontend"])
    def approval_summary() -> ApprovalSummary:
        return store.summary()

    @app.get("/approvals/{approval_id}", response_model=Approval, tags=["frontend"])
    def get_approval(approval_id: int) -> Approval:
        return store.get(approval_id)

    @app.post("/approvals/{approval_id}/approve", response_model=Approval, tags=["frontend"])
    def approve(approval_id: int, body: ApproveRequest | None = None) -> Approval:
        """pending → approved → (게시) → posted. approved 에서 부르면 게시만 다시 시도한다.

        본문 draft 가 저장된 초안과 다르면(사람이 고침) pending 에서만 받아 고친 본문을 게시하고,
        원래 초안은 edits 에 남긴다. approved(게시 재시도)에서 고친 본문이 오면 409.
        """
        edited = (body.draft or "").strip() if body is not None else ""
        with approving:
            approval = store.get(approval_id)
            if edited and edited != approval.draft.strip():
                if approval.status != S.PENDING:
                    return JSONResponse(
                        status_code=409,
                        content={
                            "error": "edit_not_allowed",
                            "detail": f"고친 초안은 pending 에서만 받는다 (지금 {approval.status})",
                        },
                    )

                def put_edit(a: Approval) -> None:
                    a.edits.append(Edit(draft=a.draft, at=now()))
                    a.draft = edited

                approval = store.advance(approval_id, Step(S.APPROVED, "human", "edited", put_edit))
            elif approval.status == S.PENDING:
                approval = store.advance(approval_id, Step(S.APPROVED, "human"))
            elif approval.status != S.APPROVED:
                raise InvalidTransition(approval.status, S.APPROVED)
            url = publish_to.publish(approval.channel, approval.target, approval.draft)

            def put_url(a: Approval) -> None:
                a.posted_url = url

            return store.advance(approval_id, Step(S.POSTED, "publisher", url, put_url))

    @app.post("/approvals/{approval_id}/reject", response_model=Approval, tags=["frontend"])
    def reject(approval_id: int, body: RejectRequest) -> Approval:
        """pending → rejected. MAX_ROUNDS 번째 거절이면 closed 까지."""

        def put_rejection(a: Approval) -> None:
            a.rejections.append(Rejection(draft=a.draft, reason=body.reason, at=now()))

        steps = [Step(S.REJECTED, "human", body.reason, put_rejection)]
        if store.get(approval_id).round >= MAX_ROUNDS:
            steps.append(Step(S.CLOSED, "system", f"{MAX_ROUNDS}회 거절"))
        return store.advance(approval_id, *steps)

    # ---- internal (desk) ---------------------------------------------------

    @app.post("/approvals", status_code=201, response_model=Approval, tags=["internal"])
    def create_approval(req: CreateApprovalRequest, response: Response) -> Approval:
        approval, created = store.create(req, who="desk")
        if not created:
            response.status_code = 200  # 같은 멘션의 안건이 이미 있음
        return approval

    @app.post("/approvals/{approval_id}/revise", response_model=Approval, tags=["internal"])
    def revise(approval_id: int, body: ReviseApprovalRequest) -> Approval:
        """rejected → pending. 거절 사유를 반영한 새 초안으로 round+1."""

        def put_draft(a: Approval) -> None:
            a.round += 1
            a.task, a.knowledge, a.refusal, a.draft = (
                body.task,
                body.knowledge,
                body.refusal,
                body.draft,
            )

        current = store.get(approval_id)
        return store.advance(
            approval_id, Step(S.PENDING, "desk", f"round {current.round + 1}", put_draft)
        )

    # ---- 참조 웹 -------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def approval_page() -> FileResponse:
        return FileResponse(INDEX_HTML, media_type="text/html; charset=utf-8")

    return app
