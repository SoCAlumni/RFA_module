"""GitHub 채널.

멘션 찾기: 감시 레포(RFA_GITHUB_REPOS)의 이슈 본문과 이슈/PR 댓글을 읽어 @<login> 이 들어간 것만.
(notifications API 는 fine-grained 토큰을 지원하지 않아 쓰지 않는다. 권한: Issues read/write)

- GithubChannel.poll : 읽기. desk 가 새 멘션을 가져올 때 (MentionTracker + 스레드 맥락)
- GithubChannel.post : 쓰기. 결재 서버가 사람 승인 뒤에만 호출

자기 답글 무시: 이 시스템이 게시하는 댓글은 GitHub 에 login 본인 이름으로 달린다. 그 댓글에 다시
반응하지 않도록 게시할 때 화면에 안 보이는 BOT_MARKER 를 붙이고, 멘션을 찾을 때 그 글만 건너뛴다.
(본인이 직접 쓴 @login 은 멘션으로 인정한다 → 혼자서도 비서를 부를 수 있다)
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from rfa_common.contracts import ChannelKind, Mention, ThreadMessage

from channels.base import CONTEXT_LIMIT, ChannelError

API_URL = "https://api.github.com"
PAGE_SIZE = 100
DEFAULT_LOOKBACK = timedelta(hours=24)
BOT_MARKER = "<!-- rfa-bot -->"
SEEN_LIMIT = 500


class GithubError(ChannelError):
    """GitHub API 가 2xx 가 아닌 응답을 줬다."""


def parse_target(target: str) -> tuple[str, int]:
    """ "owner/repo#34" → ("owner/repo", 34). 형식은 Mention 이 검증한다."""
    repo, _, number = target.partition("#")
    return repo, int(number)


@dataclass(frozen=True)
class GithubConfig:
    token: str
    login: str
    repos: tuple[str, ...]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> GithubConfig:
        missing = [
            k for k in ("GITHUB_TOKEN", "RFA_GITHUB_LOGIN", "RFA_GITHUB_REPOS") if not env.get(k)
        ]
        if missing:
            raise RuntimeError(f"missing env: {', '.join(missing)} (see .env.example)")
        repos = tuple(r.strip() for r in env["RFA_GITHUB_REPOS"].split(",") if r.strip())
        return cls(token=env["GITHUB_TOKEN"], login=env["RFA_GITHUB_LOGIN"], repos=repos)


class GithubClient:
    def __init__(self, token: str, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            transport=transport,
            timeout=15,
        )

    def _send(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """네트워크 오류도 GithubError 로 바꿔, 호출하는 쪽이 채널 오류 하나만 다루게 한다."""
        try:
            return self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise GithubError(f"{method} {path}: {type(exc).__name__}") from exc

    def _get(self, path: str, **params: object) -> list | dict:
        res = self._send("GET", path, params=params)
        if res.is_error:
            raise GithubError(f"GET {path}: {res.status_code} {res.text[:200]}")
        return res.json()

    def issues_since(self, repo: str, since: datetime) -> list[dict]:
        return self._get(
            f"/repos/{repo}/issues", since=_iso(since), state="all", per_page=PAGE_SIZE
        )

    def comments_since(self, repo: str, since: datetime) -> list[dict]:
        return self._get(
            f"/repos/{repo}/issues/comments",
            since=_iso(since),
            sort="created",
            direction="asc",
            per_page=PAGE_SIZE,
        )

    def thread(self, target: str, exclude_url: str) -> list[ThreadMessage]:
        """이슈 본문(제목 포함)과 댓글 중 최근 CONTEXT_LIMIT 개, 오래된 순. 댓글은 첫 100개만 본다.

        exclude_url 인 글(멘션 자신)은 뺀다. 우리가 단 답글은 BOT_MARKER 만 지우고 남긴다
        (이미 뭐라고 답했는지도 맥락이다).
        """
        repo, number = parse_target(target)
        issue = self._get(f"/repos/{repo}/issues/{number}")
        comments = self._get(f"/repos/{repo}/issues/{number}/comments", per_page=PAGE_SIZE)
        posts = [
            (issue, f"{issue['title']}\n{issue.get('body') or ''}".strip()),
            *((c, c.get("body") or "") for c in comments),
        ]
        return [
            ThreadMessage(
                author=item["user"]["login"],
                text=text.replace(BOT_MARKER, "").strip(),
                at=item["created_at"],
            )
            for item, text in posts
            if item["html_url"] != exclude_url
        ][-CONTEXT_LIMIT:]

    def create_comment(self, target: str, body: str) -> str:
        """댓글을 달고 html_url 을 돌려준다. 사람이 승인한 본문만 넘길 것.

        본문 끝에 BOT_MARKER 를 붙여 이후 멘션 검색에서 자기 답글을 거른다.
        """
        repo, number = parse_target(target)
        marked = f"{body}\n\n{BOT_MARKER}"
        res = self._send("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": marked})
        if res.is_error:
            raise GithubError(f"POST comment {target}: {res.status_code} {res.text[:200]}")
        return res.json()["html_url"]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mentions(login: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-])@{re.escape(login)}(?![\w-])", re.IGNORECASE)


def find_mentions(
    login: str, repo: str, issues: list[dict], comments: list[dict], since: datetime
) -> list[Mention]:
    """since 이후 새로 쓰인 이슈 본문/댓글 중 @login 이 있고, 이 시스템이 게시한 답글이 아닌 것."""
    pattern = _mentions(login)
    found: list[Mention] = []

    def consider(item: dict, number: int) -> None:
        created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        author = item["user"]["login"]
        body = item.get("body") or ""
        if created < since or BOT_MARKER in body or not pattern.search(body):
            return
        found.append(
            Mention(
                channel=ChannelKind.GITHUB,
                target=f"{repo}#{number}",
                author=author,
                text=body,
                url=item["html_url"],
                created_at=created,
            )
        )

    for issue in issues:
        consider(issue, issue["number"])
    for comment in comments:
        consider(comment, int(comment["issue_url"].rsplit("/", 1)[1]))
    return sorted(found, key=lambda m: m.created_at)


class MentionTracker:
    """이미 돌려준 멘션을 기억한다: <state_dir>/mentions_seen.json = {since, seen[url]}.

    한 번 돌려준 멘션은 다시 돌려주지 않는다(at-most-once).
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> tuple[datetime | None, list[str]]:
        if not self._path.is_file():
            return None, []
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["since"]), data["seen"]

    def poll(
        self, client: GithubClient, config: GithubConfig, since: datetime | None = None
    ) -> list[Mention]:
        started = datetime.now(UTC)
        last_since, seen = self._load()
        since = since or last_since or started - DEFAULT_LOOKBACK
        fresh: list[Mention] = []
        for repo in config.repos:
            issues = client.issues_since(repo, since)
            comments = client.comments_since(repo, since)
            for m in find_mentions(config.login, repo, issues, comments, since):
                if str(m.url) not in seen:
                    fresh.append(m)
                    seen.append(str(m.url))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"since": started.isoformat(), "seen": seen[-SEEN_LIMIT:]}
        self._path.write_text(json.dumps(payload), encoding="utf-8")
        return fresh


@dataclass
class GithubChannel:
    """Channel 구현. 멘션 폴링 + 스레드 맥락, 댓글 게시."""

    client: GithubClient
    config: GithubConfig
    tracker: MentionTracker
    kind: ChannelKind = ChannelKind.GITHUB

    @classmethod
    def from_env(cls, env: Mapping[str, str], state_dir: Path) -> GithubChannel:
        config = GithubConfig.from_env(env)
        return cls(
            GithubClient(config.token), config, MentionTracker(state_dir / "mentions_seen.json")
        )

    def start(self) -> None:
        """GitHub 는 poll 할 때마다 API 를 부르므로 미리 할 일이 없다."""

    def poll(self) -> list[Mention]:
        """새 멘션에 스레드 맥락을 붙인다. 맥락을 못 읽으면 맥락 없이 돌려준다.

        MentionTracker 는 돌려준 멘션을 바로 '봤음' 으로 기록한다 (at-most-once).
        맥락 조회 실패로 멘션을 잃지 않도록 여기서는 오류를 삼킨다.
        """
        mentions = self.tracker.poll(self.client, self.config)
        return [m.model_copy(update={"context": self._context(m)}) for m in mentions]

    def _context(self, m: Mention) -> list[ThreadMessage]:
        try:
            return self.client.thread(m.target, exclude_url=str(m.url))
        except GithubError:
            return []

    def post(self, target: str, body: str) -> str:
        return self.client.create_comment(target, body)
