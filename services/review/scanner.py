"""초안에서 비밀값을 찾는 규칙 기반 스캐너. LLM 없음.

- token         : 알려진 토큰 접두사 정규식
- private_ip    : RFC1918 사설 IPv4 (10/8, 172.16/12, 192.168/16)
- internal_host : <policy_dir>/internal_hosts.txt 의 호스트명 (대소문자 무시, 단어 경계)
- internal_path : <policy_dir>/internal_paths.txt 의 접두사로 시작하는 경로 (공백 전까지)

겹치는 hit 는 긴 것만 남긴다. 결과는 censor 에게 참고로 전달된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rfa_common.models import ScanHit, ScanType

HOSTS_FILE = "internal_hosts.txt"
PATHS_FILE = "internal_paths.txt"

_TOKEN_PATTERNS = [
    r"hf_[A-Za-z0-9]{20,}",
    r"ghp_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"sk-[A-Za-z0-9_-]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"xox[bp]-[A-Za-z0-9-]{10,}",
]
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(_TOKEN_PATTERNS) + ")")

_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
PRIVATE_IP_RE = re.compile(
    r"(?<![0-9.])(?:"
    rf"10\.{_OCTET}\.{_OCTET}\.{_OCTET}"
    rf"|172\.(?:1[6-9]|2[0-9]|3[01])\.{_OCTET}\.{_OCTET}"
    rf"|192\.168\.{_OCTET}\.{_OCTET}"
    r")(?![0-9]|\.[0-9])"
)


@dataclass(frozen=True)
class ScanRules:
    hosts: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()


def load_rules(policy_dir: Path) -> ScanRules:
    return ScanRules(
        hosts=_read_list(policy_dir / HOSTS_FILE),
        path_prefixes=_read_list(policy_dir / PATHS_FILE),
    )


def _read_list(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return tuple(line for line in lines if line and not line.startswith("#"))


def scan(text: str, rules: ScanRules) -> list[ScanHit]:
    hits = [
        *_find(ScanType.TOKEN, TOKEN_RE, text),
        *_find(ScanType.PRIVATE_IP, PRIVATE_IP_RE, text),
    ]
    for host in rules.hosts:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9.-]){re.escape(host)}(?![A-Za-z0-9-]|\.[A-Za-z0-9])", re.I
        )
        hits += _find(ScanType.INTERNAL_HOST, pattern, text)
    for prefix in rules.path_prefixes:
        hits += _find(
            ScanType.INTERNAL_PATH, re.compile(re.escape(prefix) + r"[^\s)\]'\"`,]*"), text
        )
    return _drop_overlaps(hits)


def has_token(text: str) -> bool:
    """approve 직전 최종 본문에 토큰이 남아 있는지. 남아 있으면 서버가 게시를 거부한다."""
    return TOKEN_RE.search(text) is not None


def _find(kind: ScanType, pattern: re.Pattern[str], text: str) -> list[ScanHit]:
    return [ScanHit(type=kind, match=m.group(), span=m.span()) for m in pattern.finditer(text)]


def _drop_overlaps(hits: list[ScanHit]) -> list[ScanHit]:
    kept: list[ScanHit] = []
    for hit in sorted(hits, key=lambda h: (h.span[0] - h.span[1], h.span[0])):
        start, end = hit.span
        if all(end <= k.span[0] or start >= k.span[1] for k in kept):
            kept.append(hit)
    return sorted(kept, key=lambda h: h.span[0])
