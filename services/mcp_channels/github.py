"""GitHub 채널.

멘션 찾기: 감시 레포(RFA_GITHUB_REPOS)의 이슈 본문과 이슈/PR 댓글을 읽어 @<login> 이 들어간 것만.
(notifications API 는 fine-grained 토큰을 지원하지 않아 쓰지 않는다. 권한: Issues read/write)

- list_mentions / get_thread : 읽기. MCP 툴로 노출 (server.py)
- create_comment             : 쓰기. MCP 로 노출 안 함. GithubPublisher 가 clearance 검증 후 호출
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from rfa_common.models import Channel, Mention, parse_target

from mcp_channels.models import Thread, ThreadComment

API_URL = "https://api.github.com"
PAGE_SIZE = 100
THREAD_COMMENTS = 10
DEFAULT_LOOKBACK = timedelta(hours=24)
SEEN_LIMIT = 500


class GithubError(Exception):
    """GitHub API 가 2xx 가 아닌 응답을 줬다."""


@dataclass(frozen=True)
class GithubConfig:
    token: str
    login: str
    repos: tuple[str, ...]

    @classmethod
    def from_env(cls, env: dict[str, str]) -> GithubConfig:
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

    def _get(self, path: str, **params: object) -> list | dict:
        res = self._http.get(path, params=params)
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

    def get_thread(self, target: str) -> Thread:
        repo, number = parse_target(target)
        issue = self._get(f"/repos/{repo}/issues/{number}")
        comments = self._get(f"/repos/{repo}/issues/{number}/comments", per_page=PAGE_SIZE)[
            -THREAD_COMMENTS:
        ]
        return Thread(
            target=target,
            title=issue["title"],
            body=issue.get("body") or "",
            state=issue["state"],
            author=issue["user"]["login"],
            url=issue["html_url"],
            comments=[
                ThreadComment(
                    author=c["user"]["login"],
                    body=c.get("body") or "",
                    created_at=c["created_at"],
                    url=c["html_url"],
                )
                for c in comments
            ],
        )

    def create_comment(self, target: str, body: str) -> str:
        """댓글을 달고 html_url 을 돌려준다. 호출 전에 반드시 clearance 를 검증할 것."""
        repo, number = parse_target(target)
        res = self._http.post(f"/repos/{repo}/issues/{number}/comments", json={"body": body})
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
    """since 이후 새로 쓰인 이슈 본문/댓글 중 @login 이 있고 login 본인이 쓴 게 아닌 것."""
    pattern = _mentions(login)
    found: list[Mention] = []

    def consider(item: dict, number: int) -> None:
        created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
        author = item["user"]["login"]
        body = item.get("body") or ""
        if created < since or author.lower() == login.lower() or not pattern.search(body):
            return
        found.append(
            Mention(
                channel=Channel.PUBLIC,
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
