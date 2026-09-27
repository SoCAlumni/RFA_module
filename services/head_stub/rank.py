"""질문과 문서의 키워드 겹침으로 관련 문서를 고른다. LLM 없음.

score = (질문 토큰 중 문서 본문에 부분 문자열로 등장하는 수) / (질문 토큰 수)
한국어 조사("벤치마크는")를 고려해 토큰 동일이 아니라 부분 문자열로 센다.
"""

from __future__ import annotations

import re

from head_stub.loader import Doc

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def tokenize(text: str) -> list[str]:
    return sorted({t.lower() for t in _TOKEN_RE.findall(text)})


def score(question_tokens: list[str], doc: Doc) -> float:
    if not question_tokens:
        return 0.0
    haystack = f"{doc.title}\n{doc.body}".lower()
    hits = sum(1 for t in question_tokens if t in haystack)
    return hits / len(question_tokens)


def top_docs(question: str, docs: list[Doc], k: int = 3) -> list[tuple[Doc, float]]:
    """점수 > 0 인 문서를 점수 내림차순(동점이면 최신순)으로 최대 k 개."""
    tokens = tokenize(question)
    scored = [(doc, s) for doc in docs if (s := score(tokens, doc)) > 0]
    scored.sort(key=lambda ds: (-ds[1], -ds[0].updated_at.toordinal()))
    return scored[:k]
