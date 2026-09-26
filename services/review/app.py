"""결재 문서 API. contracts/review.openapi.yaml 참고."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from rfa_common.models import (
    DraftRequest,
    KnowledgeResult,
    NeedsHumanRequest,
    OpenReviewRequest,
    Policy,
    Review,
    ReviewStatus,
    Verdict,
    VerdictDecision,
)

from review.policy import PolicyNotFound, load_policy
from review.scanner import load_rules, scan
from review.store import InvalidTransition, ReviewNotFound, ReviewStore, Step

DEFAULT_DATA_DIR = Path("./data")

Actor = Annotated[str, Header(alias="X-RFA-Actor")]


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = data_dir or Path(os.environ.get("RFA_DATA_DIR", DEFAULT_DATA_DIR))
    store = ReviewStore(root / "state")
    policy_dir = root / "policy"
    app = FastAPI(title="RFA review", version="0.1.0")

    @app.exception_handler(ReviewNotFound)
    def _not_found(_: Request, exc: ReviewNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "id": exc.review_id})

    @app.exception_handler(PolicyNotFound)
    def _no_policy(_: Request, exc: PolicyNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "scope": exc.scope})

    @app.exception_handler(InvalidTransition)
    def _conflict(_: Request, exc: InvalidTransition) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": "invalid_transition", "from": exc.current, "to": exc.to},
        )

    @app.post("/reviews", status_code=201, response_model=Review)
    def open_review(req: OpenReviewRequest, who: Actor = "unknown") -> Review:
        return store.create(req, who=who)

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


app = create_app()
