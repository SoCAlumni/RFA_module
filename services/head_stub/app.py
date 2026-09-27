"""POST /ask — contracts/head.openapi.yaml 을 지키는 stub. 민섭님 head agent 가 오면 교체된다.

진짜 head agent 가 하는 일(업무 선택, 실무 에이전트, 검열) 대신 키워드 규칙만 쓴다. LLM 없음.
  1. 모든 task 의 문서를 질문과 키워드로 대조해 1위 문서의 task 를 고른다.
  2. 그 task 안에서 상위 TOP_K 문서의 본문 문장을 이어 knowledge 로 돌려준다.
  3. feedback(거절 이력)의 모든 사유에서 키워드를 뽑아, 그 키워드가 들어간 문장을 뺀다.
     (예: 사유 "릴리즈 날짜가 들어가 있음" → '릴리즈' 가 든 문장 제거)
  4. 맞는 문서가 없거나 다 빠지면 refusal.

검열은 하지 않는다. 데모 지식에는 기밀(미공개 일정, 내부 IP, 토큰)이 일부러 섞여 있어서,
사람이 거절하고 사유가 반영되는 장면이 보인다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import FastAPI
from rfa_common.contracts import AskRequest, AskResponse, TaskRef

from head_stub.loader import load_docs, load_tasks
from head_stub.rank import tokenize, top_docs

DEFAULT_DATA_DIR = Path("./data")
TOP_K = 3
NO_TASK = "관련 업무를 찾지 못했습니다"
NOTHING_LEFT = "거절 사유를 반영하면 답할 수 있는 내용이 없습니다"

_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")  # 마침표 뒤에 공백이 올 때만 (0.6B, 10.1.2.3 보존)


def sentences(body: str) -> list[str]:
    """마크다운 본문 → 문장 목록. 제목 줄(#)은 뺀다."""
    lines = [
        ln.strip() for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    return [s.strip() for s in _SENTENCE_END.split(" ".join(lines)) if s.strip()]


def answer(root: Path, req: AskRequest) -> AskResponse:
    tasks = {t.id: t for t in load_tasks(root)}
    docs = [doc for task_id in tasks for doc in load_docs(root, task_id)]
    best = top_docs(req.question, docs, k=1)
    if not best:
        return AskResponse(knowledge="", refusal=NO_TASK)

    task = tasks[best[0][0].task_id]
    in_task = [d for d in docs if d.task_id == task.id]
    banned = {t for rejection in req.feedback for t in tokenize(rejection.reason)}
    kept = [
        s
        for doc, _ in top_docs(req.question, in_task, k=TOP_K)
        for s in sentences(doc.body)
        if not any(t in s.lower() for t in banned)
    ]
    ref = TaskRef(id=task.id, name=task.name)
    if not kept:
        return AskResponse(knowledge="", task=ref, refusal=NOTHING_LEFT)
    return AskResponse(knowledge=" ".join(kept), task=ref)


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = (data_dir or Path(os.environ.get("RFA_DATA_DIR", DEFAULT_DATA_DIR))) / "knowledge"
    app = FastAPI(title="RFA head stub", version="0.2.0")

    @app.post("/ask", response_model=AskResponse)
    def ask(req: AskRequest) -> AskResponse:
        return answer(root, req)

    return app


app = create_app()
