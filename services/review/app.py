"""결재 문서 API. contracts/review.openapi.yaml 참고."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from rfa_common.models import (
    Channel,
    Decision,
    DraftRequest,
    FeedbackDecision,
    FeedbackItem,
    KnowledgeResult,
    NeedsHumanRequest,
    OpenReviewRequest,
    Policy,
    Review,
    ReviewStatus,
    Verdict,
    VerdictDecision,
)

from review import clearance
from review.policy import FEEDBACK_FILE, PolicyNotFound, append_feedback, load_policy
from review.publisher import Publisher, PublishError, make_publisher
from review.scanner import has_token, load_rules, scan
from review.store import InvalidTransition, ReviewNotFound, ReviewStore, Step

DEFAULT_DATA_DIR = Path("./data")
INDEX_HTML = Path(__file__).parent / "static" / "index.html"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})

Actor = Annotated[str, Header(alias="X-RFA-Actor")]


class ApprovalBlocked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RejectRequest(BaseModel):
    reason: str


class ApproveResponse(BaseModel):
    status: ReviewStatus
    posted_url: str | None = None


def require_loopback(request: Request) -> None:
    """사람 결재는 호스트 브라우저(loopback)에서만. 샌드박스/원격은 403."""
    host = request.client.host if request.client else None
    if host not in LOOPBACK_HOSTS:
        raise ApprovalBlocked("loopback_only")


def create_app(
    data_dir: Path | None = None,
    clearance_key: str | None = None,
    publisher: Publisher | None = None,
) -> FastAPI:
    root = data_dir or Path(os.environ.get("RFA_DATA_DIR", DEFAULT_DATA_DIR))
    key = clearance_key or os.environ.get("RFA_CLEARANCE_KEY") or ""
    if not key:
        raise RuntimeError("RFA_CLEARANCE_KEY is required (see .env.example)")
    store = ReviewStore(root / "state")
    policy_dir = root / "policy"
    publish_to = publisher if publisher is not None else make_publisher(os.environ, key)
    app = FastAPI(title="RFA review", version="0.1.0")

    @app.exception_handler(ReviewNotFound)
    def _not_found(_: Request, exc: ReviewNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "id": exc.review_id})

    @app.exception_handler(PolicyNotFound)
    def _no_policy(_: Request, exc: PolicyNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "scope": exc.scope})

    @app.exception_handler(ApprovalBlocked)
    def _blocked(_: Request, exc: ApprovalBlocked) -> JSONResponse:
        code = 403 if exc.reason == "loopback_only" else 409
        return JSONResponse(status_code=code, content={"error": exc.reason})

    @app.exception_handler(PublishError)
    def _publish_failed(_: Request, exc: PublishError) -> JSONResponse:
        return JSONResponse(
            status_code=502, content={"error": "publish_failed", "detail": str(exc)}
        )

    @app.exception_handler(InvalidTransition)
    def _conflict(_: Request, exc: InvalidTransition) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": "invalid_transition", "from": exc.current, "to": exc.to},
        )

    @app.post("/reviews", status_code=201, response_model=Review)
    def open_review(req: OpenReviewRequest, response: Response, who: Actor = "unknown") -> Review:
        review, created = store.create(req, who=who)
        if not created:
            response.status_code = 200  # 같은 멘션의 문서가 이미 있음
        return review

    @app.get("/reviews", response_model=list[Review])
    def list_reviews(status: ReviewStatus | None = None) -> list[Review]:
        return store.list(status)

    @app.get("/reviews/{review_id}", response_model=Review)
    def get_review(review_id: int) -> Review:
        return store.get(review_id)

    @app.post("/reviews/{review_id}/knowledge", response_model=Review)
    def attach_knowledge(review_id: int, body: KnowledgeResult, who: Actor = "unknown") -> Review:
        def apply(r: Review) -> None:
            r.knowledge = body

        return store.advance(
            review_id, Step(ReviewStatus.KNOWLEDGE_READY, who, detail=body.task_id, mutate=apply)
        )

    @app.post("/reviews/{review_id}/draft", response_model=Review)
    def submit_draft(review_id: int, body: DraftRequest, who: Actor = "unknown") -> Review:
        hits = scan(body.text, load_rules(policy_dir))

        def put_draft(r: Review) -> None:
            r.draft = body.text
            r.edit_log = body.edit_log

        def put_scan(r: Review) -> None:
            r.scan = hits

        return store.advance(
            review_id,
            Step(ReviewStatus.DRAFTED, who, mutate=put_draft),
            Step(ReviewStatus.SCANNED, "scanner", detail=f"{len(hits)} hits", mutate=put_scan),
        )

    @app.post("/reviews/{review_id}/verdict", response_model=Review)
    def submit_verdict(review_id: int, body: Verdict, who: Actor = "unknown") -> Review:
        def apply(r: Review) -> None:
            r.verdict = body
            r.final_body = final_body(r.draft, body)

        return store.advance(
            review_id, Step(ReviewStatus.REVIEWED, who, detail=body.verdict, mutate=apply)
        )

    @app.post("/reviews/{review_id}/needs-human", response_model=Review)
    def mark_needs_human(review_id: int, body: NeedsHumanRequest, who: Actor = "unknown") -> Review:
        return store.advance(review_id, Step(ReviewStatus.NEEDS_HUMAN, who, detail=body.reason))

    def record_feedback(review: Review, decision: FeedbackDecision, reason: str | None) -> None:
        """reject 는 전부, approve 는 verdict 가 redact 였던 것만 기록한다."""
        if decision == FeedbackDecision.APPROVE and (
            review.verdict is None or review.verdict.verdict != VerdictDecision.REDACT
        ):
            return
        excerpt = (review.final_body or review.draft or "")[:120]
        append_feedback(
            policy_dir / FEEDBACK_FILE,
            FeedbackItem(
                at=datetime.now(UTC),
                scope=Channel(review.channel),
                review_id=review.id,
                decision=decision,
                reason=reason,
                draft_excerpt=excerpt or None,
            ),
        )

    @app.post(
        "/reviews/{review_id}/approve",
        response_model=ApproveResponse,
        dependencies=[Depends(require_loopback)],
    )
    def approve(review_id: int) -> ApproveResponse:
        current = store.get(review_id)
        if current.status == ReviewStatus.REVIEWED:  # 아니면 아래 advance 가 409 를 만든다
            if current.verdict is not None and current.verdict.verdict == VerdictDecision.BLOCK:
                raise ApprovalBlocked("verdict_is_block")
            if current.final_body is None or has_token(current.final_body):
                raise ApprovalBlocked("secrets_in_final_body")

        def put_decision(r: Review) -> None:
            r.decision = Decision(by="human", at=datetime.now(UTC))

        review = store.advance(review_id, Step(ReviewStatus.APPROVED, "human", mutate=put_decision))
        assert review.final_body is not None
        token = clearance.sign(key, review.id, review.target, review.final_body)
        posted_url = publish_to.publish(review.target, review.final_body, token)
        review = store.advance(review_id, Step(ReviewStatus.POSTED, "publisher", detail=posted_url))
        record_feedback(review, FeedbackDecision.APPROVE, None)
        return ApproveResponse(status=review.status, posted_url=posted_url)

    @app.post(
        "/reviews/{review_id}/reject",
        response_model=Review,
        dependencies=[Depends(require_loopback)],
    )
    def reject(review_id: int, body: RejectRequest) -> Review:
        def put_decision(r: Review) -> None:
            r.decision = Decision(by="human", at=datetime.now(UTC), reason=body.reason)

        review = store.advance(
            review_id, Step(ReviewStatus.REJECTED, "human", detail=body.reason, mutate=put_decision)
        )
        record_feedback(review, FeedbackDecision.REJECT, body.reason)
        return review

    @app.get("/", include_in_schema=False)
    def approval_page() -> FileResponse:
        """사람이 보는 알람/결재 화면. 호스트 브라우저에서 http://127.0.0.1:8790/"""
        return FileResponse(INDEX_HTML, media_type="text/html; charset=utf-8")

    @app.get("/policy/{scope}", response_model=Policy)
    def get_policy(scope: str) -> Policy:
        return load_policy(policy_dir, scope)

    return app


def final_body(draft: str | None, verdict: Verdict) -> str | None:
    """게시될 본문. allow → 초안, redact → 수정본, block → 없음."""
    match verdict.verdict:
        case VerdictDecision.ALLOW:
            return draft
        case VerdictDecision.REDACT:
            return verdict.redacted_body
        case VerdictDecision.BLOCK:
            return None
