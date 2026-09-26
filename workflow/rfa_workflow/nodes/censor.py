"""기밀검토 2-B (Public, CODE 관리자). 공식·개인 기준 + 과거 결재 사례 + 스캐너 결과로 판정한다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError
from rfa_common.models import Verdict

from rfa_workflow.deps import Deps
from rfa_workflow.llm import LLMError, prompt
from rfa_workflow.state import State

SCOPE = "public"
ATTEMPTS = 2  # 형식이 틀리면 오류를 알려 주고 한 번 더 시킨다


class ReasonOut(BaseModel):
    rule: str
    span: str
    action: Literal["remove", "blur", "keep"]


class CensorOutput(BaseModel):
    verdict: Literal["allow", "redact", "block"]
    redacted_body: str
    reasons: list[ReasonOut]
    summary: str


def _to_verdict(out: CensorOutput) -> Verdict:
    return Verdict.model_validate(
        {
            "verdict": out.verdict,
            "redacted_body": out.redacted_body or None,
            "reasons": [r.model_dump() for r in out.reasons],
            "summary": out.summary,
        }
    )


def censor_public(state: State, deps: Deps) -> dict:
    policy = deps.review.policy(SCOPE)
    feedback = (
        "\n".join(
            f"- [{f.decision}] {f.reason or ''} (초안 일부: {f.draft_excerpt or ''})"
            for f in policy.feedback
        )
        or "- (없음)"
    )
    scan = state.get("scan", [])
    hits = "\n".join(f"- {h.type}: {h.match}" for h in scan) or "- (없음)"
    sources = "\n".join(f"- {s}" for s in state["knowledge"].sources) or "- (없음)"
    user = (
        f"[공식 기준]\n{policy.official}\n\n[개인 기준]\n{policy.personal or '(없음)'}\n\n"
        f"[과거 결정 사례]\n{feedback}\n\n[스캐너 결과]\n{hits}\n\n[근거 출처]\n{sources}\n\n"
        f"[초안]\n{state['draft']}"
    )
    error = ""
    for _ in range(ATTEMPTS):
        out = deps.llm.structured(
            name="censor_public",
            system=prompt("censor_public"),
            user=user + error,
            schema=CensorOutput,
            context={"draft": state["draft"], "scan": scan},
        )
        try:
            verdict = _to_verdict(out)
        except ValidationError as exc:
            error = f"\n\n[이전 출력 오류 — 형식을 고쳐 다시 출력]\n{exc.errors()[0]['msg']}"
            continue
        deps.review.submit_verdict(state["review_id"], verdict)
        return {"verdict": verdict, "outcome": "reviewed"}
    raise LLMError(f"censor_public: invalid verdict after {ATTEMPTS} attempts ({error.strip()})")
