from datetime import datetime
from pathlib import Path

import yaml

from kb.models import Fact
from kb.util import scope_dir

_INDEXED_DIRS = ("memory", "wiki", "decisions")


def fact_to_markdown(fact: Fact) -> str:
    meta = {
        "id": fact.id,
        "scope": fact.scope,
        "tags": fact.tags,
        "source": fact.source,
        "ts": fact.ts.isoformat(),
        "content_hash": fact.content_hash,
        "superseded_by": fact.superseded_by,
    }
    front = yaml.safe_dump(meta, sort_keys=True, default_flow_style=False).strip()
    return f"---\n{front}\n---\n\n{fact.content}\n"


def markdown_to_fact(text: str, path: str) -> Fact:
    assert text.startswith("---"), f"missing front-matter in {path}"
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front) or {}
    return Fact(
        id=meta.get("id", ""),
        scope=meta.get("scope", "global"),
        content=body.strip(),
        tags=list(meta.get("tags") or []),
        source=meta.get("source"),
        ts=datetime.fromisoformat(meta["ts"]) if meta.get("ts") else None,  # type: ignore[arg-type]
        content_hash=meta.get("content_hash", ""),
        superseded_by=meta.get("superseded_by"),
        path=path,
    )


def write_fact(repo_path: Path, fact: Fact) -> Path:
    target_dir = repo_path / "memory" / scope_dir(fact.scope)
    target_dir.mkdir(parents=True, exist_ok=True)
    p = target_dir / f"{fact.id}.md"
    p.write_text(fact_to_markdown(fact))
    fact.path = str(p)
    return p


def read_all_facts(repo_path: Path, include_sources: bool = False) -> list[Fact]:
    dirs = list(_INDEXED_DIRS) + (["sources"] if include_sources else [])
    facts: list[Fact] = []
    for d in dirs:
        base = repo_path / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.md")):
            text = p.read_text()
            if text.lstrip().startswith("---"):
                facts.append(markdown_to_fact(text, str(p)))
            else:
                # non-fact curated page (e.g. wiki index): index as plain content
                facts.append(Fact(id=str(p), scope="global", content=text.strip(),
                                  tags=[], source=str(p), ts=None, content_hash=""))  # type: ignore[arg-type]
    return facts


def append_log(repo_path: Path, line: str) -> None:
    log = repo_path / "log.md"
    with log.open("a") as fh:
        fh.write(line.rstrip() + "\n")


def _pending_dir(repo_path: Path) -> Path:
    d = repo_path / ".kb" / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_pending_marker(repo_path: Path, fact_id: str) -> None:
    (_pending_dir(repo_path) / fact_id).write_text("")


def read_pending_markers(repo_path: Path) -> list[str]:
    d = _pending_dir(repo_path)
    return [p.name for p in d.iterdir() if p.is_file()]


def clear_pending_marker(repo_path: Path, fact_id: str) -> None:
    p = _pending_dir(repo_path) / fact_id
    if p.exists():
        p.unlink()
