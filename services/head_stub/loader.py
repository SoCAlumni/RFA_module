"""data/knowledge/<task_id>/ 폴더에서 task 정보와 지식 문서를 읽는다.

폴더 규칙:
  _task.yaml   : {name, description, updated_at}  → TaskInfo. 없는 폴더는 task 가 아님.
  *.md         : 맨 앞에 YAML frontmatter {title, updated_at}. 없으면 건너뜀. (다른 키는 무시)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

TASK_FILE = "_task.yaml"
FRONTMATTER_DELIM = "---"


class TaskInfo(BaseModel):
    id: str
    name: str
    description: str
    updated_at: date


@dataclass
class Doc:
    task_id: str
    title: str
    updated_at: date
    body: str


def load_tasks(data_dir: Path) -> list[TaskInfo]:
    tasks: list[TaskInfo] = []
    for task_dir in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        meta_path = task_dir / TASK_FILE
        if not meta_path.is_file():
            continue
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        try:
            tasks.append(TaskInfo(id=task_dir.name, **meta))
        except (ValidationError, TypeError) as exc:
            log.warning("skip task %s: invalid %s (%s)", task_dir.name, TASK_FILE, exc)
    return tasks


def load_docs(data_dir: Path, task_id: str) -> list[Doc]:
    task_dir = data_dir / task_id
    if not (task_dir / TASK_FILE).is_file():
        return []
    docs: list[Doc] = []
    for md_path in sorted(task_dir.glob("*.md")):
        doc = _parse_doc(task_id, md_path)
        if doc is not None:
            docs.append(doc)
    return docs


def _parse_doc(task_id: str, md_path: Path) -> Doc | None:
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith(FRONTMATTER_DELIM):
        log.warning("skip %s: no frontmatter", md_path.name)
        return None
    _, _, rest = text.partition(FRONTMATTER_DELIM)
    header, delim, body = rest.partition(FRONTMATTER_DELIM)
    if not delim:
        log.warning("skip %s: unterminated frontmatter", md_path.name)
        return None
    meta = yaml.safe_load(header) or {}
    try:
        return Doc(
            task_id=task_id,
            title=str(meta["title"]),
            updated_at=_as_date(meta["updated_at"]),
            body=body.strip(),
        )
    except (KeyError, ValueError) as exc:
        log.warning("skip %s: bad frontmatter (%s)", md_path.name, exc)
        return None


def _as_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
