"""테스트용 가짜 GitHub REST API (httpx.MockTransport)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

REPO = "zetwhite/rfa-test"
API = "https://api.github.com"


def user(login: str) -> dict:
    return {"login": login}


def issue(number: int, body: str, author: str = "someone", created: str = "2026-09-26T10:00:00Z"):
    return {
        "number": number,
        "title": f"issue {number}",
        "body": body,
        "state": "open",
        "user": user(author),
        "created_at": created,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
    }


def comment(
    cid: int, number: int, body: str, author: str = "someone", created: str = "2026-09-26T10:05:00Z"
):
    return {
        "id": cid,
        "body": body,
        "user": user(author),
        "created_at": created,
        "html_url": f"https://github.com/{REPO}/issues/{number}#issuecomment-{cid}",
        "issue_url": f"{API}/repos/{REPO}/issues/{number}",
    }


def notification(
    nid: str,
    number: int,
    reason: str = "mention",
    cid: int | None = None,
    updated: str = "2026-09-26T10:05:00Z",
    repo: str = REPO,
    type_: str = "Issue",
):
    """알림 스레드 하나. cid 를 주면 그 댓글이, 없으면 이슈 본체가 알림의 원인 글."""
    kind = "pulls" if type_ == "PullRequest" else "issues"
    return {
        "id": nid,
        "reason": reason,
        "unread": True,
        "updated_at": updated,
        "subject": {
            "title": f"issue {number}",
            "url": f"{API}/repos/{repo}/{kind}/{number}",
            "latest_comment_url": f"{API}/repos/{repo}/issues/comments/{cid}" if cid else None,
            "type": type_,
        },
        "repository": {"full_name": repo},
    }


@dataclass
class FakeGithub:
    issues: list[dict] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    notifications: list[dict] = field(default_factory=list)
    me: str = "zetwhite"
    poll_interval: int = 60
    fail_post: int | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    posted: list[tuple[str, str]] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/user":
            return httpx.Response(200, json=user(self.me))
        if request.method == "GET" and path == "/notifications":
            page = int(request.url.params.get("page", 1))
            per_page = int(request.url.params.get("per_page", 50))
            batch = self.notifications[(page - 1) * per_page : page * per_page]
            return httpx.Response(
                200, json=batch, headers={"X-Poll-Interval": str(self.poll_interval)}
            )
        if request.method == "GET" and path.startswith(f"/repos/{REPO}/issues/comments/"):
            cid = int(path.rsplit("/", 1)[1])
            found = [c for c in self.comments if c["id"] == cid]
            return httpx.Response(200, json=found[0]) if found else httpx.Response(404, json={})
        if request.method == "GET" and path == f"/repos/{REPO}/issues":
            return httpx.Response(200, json=self.issues)
        if request.method == "GET" and path == f"/repos/{REPO}/issues/comments":
            return httpx.Response(200, json=self.comments)
        if path.startswith(f"/repos/{REPO}/issues/") or path.startswith(f"/repos/{REPO}/pulls/"):
            rest = path.split("/repos/")[1].split("/")[2:]  # ["issues", n, ...]
            number = int(rest[1])
            if request.method == "GET" and len(rest) == 2:
                found = [i for i in self.issues if i["number"] == number]
                return httpx.Response(200, json=found[0]) if found else httpx.Response(404, json={})
            if request.method == "GET" and rest[2:] == ["comments"]:
                return httpx.Response(
                    200, json=[c for c in self.comments if c["issue_url"].endswith(f"/{number}")]
                )
            if request.method == "POST" and rest[2:] == ["comments"]:
                if self.fail_post:
                    return httpx.Response(self.fail_post, json={"message": "nope"})
                body = json.loads(request.content)["body"]
                self.posted.append((f"{REPO}#{number}", body))
                return httpx.Response(
                    201,
                    json={
                        "html_url": f"https://github.com/{REPO}/issues/{number}#issuecomment-999"
                    },
                )
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def paths(self, name: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == name]
