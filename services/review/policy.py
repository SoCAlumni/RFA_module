"""기밀 기준 읽기: <policy_dir>/<scope>/official.md, personal.md + feedback.jsonl.

feedback.jsonl 은 사람이 결재할 때마다 쌓이는 런타임 파일(gitignore). Step 5 에서 쓴다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError
from rfa_common.models import Channel, FeedbackDecision, FeedbackItem, Policy

log = logging.getLogger(__name__)

FEEDBACK_FILE = "feedback.jsonl"
FEEDBACK_LIMIT = 10


def append_feedback(path: Path, item: FeedbackItem) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(item.model_dump_json() + "\n")


class PolicyNotFound(Exception):
    def __init__(self, scope: str) -> None:
        super().__init__(f"no policy for scope {scope!r}")
        self.scope = scope


def load_policy(policy_dir: Path, scope: str) -> Policy:
    """scope 폴더에 official.md 가 없으면 PolicyNotFound. personal.md 는 없으면 빈 문자열."""
    if scope not in {c.value for c in Channel}:
        raise PolicyNotFound(scope)
    channel = Channel(scope)
    official = policy_dir / channel / "official.md"
    if not official.is_file():
        raise PolicyNotFound(scope)
    personal = policy_dir / channel / "personal.md"
    return Policy(
        scope=channel,
        official=official.read_text(encoding="utf-8"),
        personal=personal.read_text(encoding="utf-8") if personal.is_file() else "",
        feedback=recent_feedback(policy_dir / FEEDBACK_FILE, channel),
    )


def recent_feedback(path: Path, scope: Channel, limit: int = FEEDBACK_LIMIT) -> list[FeedbackItem]:
    """scope 가 같은 항목 중 reject 먼저, 그 안에서 최신순으로 limit 개."""
    if not path.is_file():
        return []
    items: list[FeedbackItem] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = FeedbackItem.model_validate_json(line)
        except ValidationError as exc:
            log.warning("skip %s:%d (%s)", path.name, n, exc.error_count())
            continue
        if item.scope == scope:
            items.append(item)
    items.sort(key=lambda i: i.at, reverse=True)
    items.sort(key=lambda i: i.decision != FeedbackDecision.REJECT)
    return items[:limit]
