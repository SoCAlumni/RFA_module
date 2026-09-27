"""GitHub 채널 — 두 가지 받기 모드 (RFA_GITHUB_MODE, 기본 mentions).

mentions (기본, 기존 동작 그대로):
    감시 레포(RFA_GITHUB_REPOS)의 이슈 본문과 댓글을 매 poll 마다 읽어 @<나> 가 들어간 글을
    찾는다. 남이 쓴 것도 내가 쓴 것도 인정한다 (혼자서도 비서를 부를 수 있다).
    fine-grained PAT 로 충분 (권한: 감시 레포의 Issues read/write).

notifications (opt-in):
    내 알림함(GET /notifications?participating=true)을 폴링한다 — 남이 보낸 멘션, 내가 연
    이슈/PR 에 달린 댓글, 담당 지정, 리뷰 요청을 레포에 상관없이 받는다. GitHub 은 자기 행동에
    알림을 주지 않으므로, 셀프 멘션은 RFA_GITHUB_REPOS 레포에서 "내가 쓴 @나" 글만 스캔으로
    보탠다 (repos 를 비우면 알림만). 폴링 주기는 알림 응답의 X-Poll-Interval(기본 60초)을
    따른다 — desk 가 5초마다 불러도 그 간격 안에서는 API 를 부르지 않는다. 읽음 처리는 하지
    않는다 (내 알림함 배지는 그대로, 중복은 로컬 기록으로 거른다).
    classic PAT 필요 — 알림 API 는 fine-grained 토큰을 지원하지 않는다
    (권한: notifications + public_repo, 비공개 레포는 repo).

내 아이디는 RFA_GITHUB_LOGIN 이 있으면 그 값, 없으면 GET /user 로 자동 감지한다.

- GithubChannel.poll : 읽기. desk 가 새 멘션을 가져올 때 (+ 스레드 맥락)
- GithubChannel.post : 쓰기. 결재 서버가 사람 승인 뒤에만 호출

자기 답글 무시: 이 시스템이 게시하는 댓글은 GitHub 에 내 이름으로 달린다. 게시할 때 화면에
안 보이는 BOT_MARKER 를 붙이고, 두 모드 모두 그 글은 건너뛴다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from rfa_common.contracts import ChannelKind, Mention, ThreadMessage

from channels.base import CONTEXT_LIMIT, ChannelError

API_URL = "https://api.github.com"
PAGE_SIZE = 100
NOTIFICATIONS_PAGE = 50  # 알림 API 의 per_page 최대값
DEFAULT_POLL_INTERVAL = 60  # X-Poll-Interval 이 없을 때 (초)
DEFAULT_LOOKBACK = timedelta(hours=24)
BOT_MARKER = "<!-- rfa-bot -->"
SEEN_LIMIT = 500
MODES = ("mentions", "notifications")

# 질문·요청으로 볼 알림 사유. participating=true 로 이미 "나에게 온 것"만 오지만,
# 그중에서도 글이 아닌 것(ci_activity, state_change, security_* 등)은 뺀다.
REASONS = frozenset(
    {
        "mention",
        "team_mention",
        "author",
        "comment",
        "assign",
        "review_requested",
        "approval_requested",
    }
)
SUBJECT_TYPES = frozenset({"Issue", "PullRequest"})


class GithubError(ChannelError):
    """GitHub API 가 2xx 가 아닌 응답을 줬다."""


def parse_target(target: str) -> tuple[str, int]:
    """ "owner/repo#34" → ("owner/repo", 34). 형식은 Mention 이 검증한다."""
    repo, _, number = target.partition("#")
    return repo, int(number)


@dataclass(frozen=True)
class GithubConfig:
    token: str
    repos: tuple[str, ...]
    mode: str = "mentions"
    login: str | None = None  # RFA_GITHUB_LOGIN. 없으면 start() 가 자동 감지

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> GithubConfig:
        mode = env.get("RFA_GITHUB_MODE") or "mentions"
        if mode not in MODES:
            raise RuntimeError(f"unknown RFA_GITHUB_MODE: {mode!r} (mentions | notifications)")
        if not env.get("GITHUB_TOKEN"):
            raise RuntimeError("missing env: GITHUB_TOKEN (see .env.example)")
        repos = tuple(r.strip() for r in env.get("RFA_GITHUB_REPOS", "").split(",") if r.strip())
        if mode == "mentions" and not repos:
            raise RuntimeError("missing env: RFA_GITHUB_REPOS (see .env.example)")
        return cls(
            token=env["GITHUB_TOKEN"],
            repos=repos,
            mode=mode,
            login=env.get("RFA_GITHUB_LOGIN") or None,
        )


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

    def me(self) -> str:
        """토큰 주인의 login (Slack 의 auth.test 에 해당)."""
        return self._get("/user")["login"]

    def notifications(self, since: datetime) -> tuple[list[dict], int]:
        """participating 알림 전부(페이지네이션)와 GitHub 이 정한 다음 폴링 간격(초)."""
        threads: list[dict] = []
        interval = DEFAULT_POLL_INTERVAL
        page = 1
        while True:
            res = self._send(
                "GET",
                "/notifications",
                params={
                    "participating": "true",
                    "since": _iso(since),
                    "per_page": NOTIFICATIONS_PAGE,
                    "page": page,
                },
            )
            if res.is_error:
                raise GithubError(f"GET /notifications: {res.status_code} {res.text[:200]}")
            interval = int(res.headers.get("X-Poll-Interval", DEFAULT_POLL_INTERVAL))
            batch = res.json()
            threads += batch
            if len(batch) < NOTIFICATIONS_PAGE:
                return threads, interval
            page += 1

    def fetch(self, api_url: str) -> dict:
        """알림 subject 의 절대 URL(댓글 또는 이슈/PR 본체)을 가져온다."""
        return self._get(api_url)

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

        본문 끝에 BOT_MARKER 를 붙여 이후 알림·스캔에서 자기 답글을 거른다.
        """
        repo, number = parse_target(target)
        marked = f"{body}\n\n{BOT_MARKER}"
        res = self._send("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": marked})
        if res.is_error:
            raise GithubError(f"POST comment {target}: {res.status_code} {res.text[:200]}")
        return res.json()["html_url"]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _created(item: dict) -> datetime:
    return datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))


# ---- 알림 → 멘션 (notifications 모드) -----------------------------------------------


class NotificationTracker:
    """알림함을 폴링해 새 알림을 Mention 으로 바꾼다. 상태: <state_dir>/notifications_seen.json.

    seen 은 {스레드 id: updated_at} — 같은 스레드라도 updated_at 이 바뀌면(새 댓글) 다시 받는다.
    읽음 처리는 하지 않는다.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> tuple[datetime | None, dict[str, str]]:
        if not self._path.is_file():
            return None, {}
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["since"]), data["seen"]

    def poll(
        self, client: GithubClient, repos: tuple[str, ...], login: str
    ) -> tuple[list[Mention], int]:
        started = datetime.now(UTC)
        last_since, seen = self._load()
        since = last_since or started - DEFAULT_LOOKBACK
        threads, interval = client.notifications(since)
        fresh = []
        for thread in threads:
            mention = self._consider(client, thread, repos, login, seen)
            if mention is not None:
                fresh.append(mention)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"since": started.isoformat(), "seen": dict(list(seen.items())[-SEEN_LIMIT:])}
        self._path.write_text(json.dumps(payload), encoding="utf-8")
        return sorted(fresh, key=lambda m: m.created_at), interval

    def _consider(
        self,
        client: GithubClient,
        thread: dict,
        repos: tuple[str, ...],
        login: str,
        seen: dict[str, str],
    ) -> Mention | None:
        subject, repo = thread["subject"], thread["repository"]["full_name"]
        if subject["type"] not in SUBJECT_TYPES or thread["reason"] not in REASONS:
            return None
        if repos and repo not in repos:
            return None
        if seen.get(thread["id"]) == thread["updated_at"]:
            return None  # 이미 처리한 시점 그대로
        seen[thread["id"]] = thread["updated_at"]

        # 알림을 만든 글: 최신 댓글, 없으면 이슈/PR 본체
        item = client.fetch(subject["latest_comment_url"] or subject["url"])
        if "title" in item:  # 이슈/PR 본체
            text = f"{item['title']}\n{item.get('body') or ''}".strip()
        else:  # 댓글
            text = item.get("body") or ""
        author = item["user"]["login"]
        if BOT_MARKER in text or author == login:
            return None  # 우리가 단 답글, 또는 내 글 (내 글은 셀프 스캔 몫)
        return Mention(
            channel=ChannelKind.GITHUB,
            target=f"{repo}#{subject['url'].rsplit('/', 1)[1]}",
            author=author,
            text=text,
            url=item["html_url"],
            created_at=_created(item),
        )


# ---- 멘션 스캔 (mentions 모드 전체 / notifications 모드의 셀프 멘션) ---------------------


def _mentions(login: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-])@{re.escape(login)}(?![\w-])", re.IGNORECASE)


def find_mentions(
    login: str,
    repo: str,
    issues: list[dict],
    comments: list[dict],
    since: datetime,
    self_only: bool = False,
) -> list[Mention]:
    """since 이후 새로 쓰인 이슈 본문/댓글 중 @login 이 있고, 비서가 게시한 답글이 아닌 것.

    self_only 면 내가 쓴 글만 받는다 — notifications 모드에서 남의 글은 알림으로 오므로
    스캔이 다시 받으면 이중 접수가 되기 때문.
    """
    pattern = _mentions(login)
    found: list[Mention] = []

    def consider(item: dict, number: int) -> None:
        created = _created(item)
        author = item["user"]["login"]
        body = item.get("body") or ""
        if created < since or BOT_MARKER in body or not pattern.search(body):
            return
        if self_only and author != login:
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
        self,
        client: GithubClient,
        repos: tuple[str, ...],
        login: str,
        self_only: bool = False,
        since: datetime | None = None,
    ) -> list[Mention]:
        started = datetime.now(UTC)
        last_since, seen = self._load()
        since = since or last_since or started - DEFAULT_LOOKBACK
        fresh: list[Mention] = []
        for repo in repos:
            issues = client.issues_since(repo, since)
            comments = client.comments_since(repo, since)
            for m in find_mentions(login, repo, issues, comments, since, self_only):
                if str(m.url) not in seen:
                    fresh.append(m)
                    seen.append(str(m.url))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"since": started.isoformat(), "seen": seen[-SEEN_LIMIT:]}
        self._path.write_text(json.dumps(payload), encoding="utf-8")
        return fresh


# ---- 채널 -------------------------------------------------------------------------


@dataclass
class GithubChannel:
    """Channel 구현. 모드에 따라 멘션 스캔 또는 알림+셀프 스캔, 스레드 맥락, 댓글 게시."""

    client: GithubClient
    config: GithubConfig
    notifications: NotificationTracker
    mentions: MentionTracker
    clock: Callable[[], float] = time.monotonic
    kind: ChannelKind = ChannelKind.GITHUB
    login: str | None = None  # start() 에서 채움
    _poll_after: float = field(default=0.0, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str], state_dir: Path) -> GithubChannel:
        config = GithubConfig.from_env(env)
        return cls(
            GithubClient(config.token),
            config,
            NotificationTracker(state_dir / "notifications_seen.json"),
            MentionTracker(state_dir / "mentions_seen.json"),
        )

    def start(self) -> None:
        """내 login 을 확정한다: RFA_GITHUB_LOGIN 이 있으면 그 값, 없으면 GET /user 로."""
        self.login = self.config.login or self.client.me()

    def _login(self) -> str:
        if self.login is None:
            self.start()
        return self.login

    def poll(self) -> list[Mention]:
        """모드별 새 멘션에 스레드 맥락을 붙인다.

        - mentions: 매 poll 마다 레포 스캔 (기존 동작 그대로)
        - notifications: X-Poll-Interval 간격으로 알림 + (repos 가 있으면) 셀프 멘션 스캔
        트래커는 돌려준 멘션을 바로 '봤음' 으로 기록한다 (at-most-once).
        맥락 조회 실패로 멘션을 잃지 않도록 _context 는 오류를 삼킨다.
        """
        if self.config.mode == "mentions":
            found = self.mentions.poll(self.client, self.config.repos, self._login())
        else:
            if self.clock() < self._poll_after:
                return []
            found, interval = self.notifications.poll(self.client, self.config.repos, self._login())
            self._poll_after = self.clock() + interval
            if self.config.repos:
                found += self.mentions.poll(
                    self.client, self.config.repos, self._login(), self_only=True
                )
        return [m.model_copy(update={"context": self._context(m)}) for m in found]

    def _context(self, m: Mention) -> list[ThreadMessage]:
        try:
            return self.client.thread(m.target, exclude_url=str(m.url))
        except GithubError:
            return []

    def post(self, target: str, body: str) -> str:
        return self.client.create_comment(target, body)
