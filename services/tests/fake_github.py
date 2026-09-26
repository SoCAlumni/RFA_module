"""테스트용 가짜 GitHub REST API (httpx.MockTransport)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

REPO = "zetwhite/rfa-test"


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
        "issue_url": f"https://api.github.com/repos/{REPO}/issues/{number}",
    }


@dataclass
class FakeGithub:
    issues: list[dict] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    fail_post: int | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    posted: list[tuple[str, str]] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == f"/repos/{REPO}/issues":
            return httpx.Response(200, json=self.issues)
        if request.method == "GET" and path == f"/repos/{REPO}/issues/comments":
            return httpx.Response(200, json=self.comments)
        if path.startswith(f"/repos/{REPO}/issues/"):
            rest = path.removeprefix(f"/repos/{REPO}/issues/").split("/")
            number = int(rest[0])
            if request.method == "GET" and len(rest) == 1:
                found = [i for i in self.issues if i["number"] == number]
                return httpx.Response(200, json=found[0]) if found else httpx.Response(404, json={})
            if request.method == "GET" and rest[1:] == ["comments"]:
                return httpx.Response(
                    200, json=[c for c in self.comments if c["issue_url"].endswith(f"/{number}")]
                )
            if request.method == "POST" and rest[1:] == ["comments"]:
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
