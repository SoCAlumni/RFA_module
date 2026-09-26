"""GET /tasks, POST /tasks/{task_id}/ask — contracts/knowledge.openapi.yaml 구현(stub)."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rfa_common.models import KnowledgeResult, TaskInfo

from knowledge_stub.loader import TASK_FILE, load_docs, load_tasks
from knowledge_stub.rank import top_docs

DEFAULT_DATA_DIR = Path("./data")
TOP_K = 3


class AskRequest(BaseModel):
    question: str


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = (data_dir or Path(os.environ.get("RFA_DATA_DIR", DEFAULT_DATA_DIR))) / "knowledge"
    app = FastAPI(title="RFA knowledge stub", version="0.1.0")

    @app.get("/tasks", response_model=list[TaskInfo])
    def list_tasks() -> list[TaskInfo]:
        return load_tasks(root)

    @app.post("/tasks/{task_id}/ask", response_model=KnowledgeResult)
    def ask_task(task_id: str, req: AskRequest) -> KnowledgeResult:
        if not (root / task_id / TASK_FILE).is_file():
            raise HTTPException(status_code=404, detail=f"unknown task: {task_id}")
        picked = top_docs(req.question, load_docs(root, task_id), k=TOP_K)
        return KnowledgeResult(
            task_id=task_id,
            answer="\n\n".join(doc.body for doc, _ in picked),
            confidence=picked[0][1] if picked else 0.0,
            sources=[doc.source_line for doc, _ in picked],
        )

    return app


app = create_app()
