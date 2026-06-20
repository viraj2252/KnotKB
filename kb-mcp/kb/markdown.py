from datetime import datetime, date, timezone
from pathlib import Path

import yaml

from kb.models import Fact
from kb.util import scope_dir

_INDEXED_DIRS = ("memory", "wiki", "decisions", "entities")


def _file_mtime(path: str | None) -> datetime:
    """Tz-aware UTC mtime for a path; falls back to now() if unavailable.
    Used when a page has front-matter (e.g. Obsidian's created/updated) but no
    `ts` key — Obsidian stamps every note, so this prevents ts=None on reindex."""
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc)
    except (OSError, TypeError):
        return datetime.now(timezone.utc)


def _coerce_dt(value) -> datetime | None:
    """Parse a front-matter datetime tolerantly. Our writer emits an ISO string,
    but Obsidian normalizes front-matter and YAML then loads timestamps as native
    datetime/date objects — accept all three (str, datetime, date)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value))


def fact_to_markdown(fact: Fact) -> str:
    meta = {
        "id": fact.id,
        "scope": fact.scope,
        "tags": fact.tags,
        "source": fact.source,
        "ts": fact.ts.isoformat() if fact.ts else None,
        "expires_at": fact.expires_at.isoformat() if fact.expires_at else None,
        "content_hash": fact.content_hash,
        "superseded_by": fact.superseded_by,
        "slug": fact.slug,
        "aliases": fact.aliases,
        "entities": fact.entities,
        "type": fact.entity_type,
        "extracted": fact.extracted,
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
        ts=_coerce_dt(meta.get("ts")) or _file_mtime(path),
        expires_at=_coerce_dt(meta.get("expires_at")),
        content_hash=meta.get("content_hash", ""),
        superseded_by=meta.get("superseded_by"),
        path=path,
        slug=meta.get("slug"),
        aliases=list(meta.get("aliases") or []),
        entities=list(meta.get("entities") or []),
        entity_type=meta.get("type"),
        extracted=bool(meta.get("extracted", False)),
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
                                  tags=[], source=str(p),
                                  ts=datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc),
                                  content_hash="", slug=p.stem))
    return facts


def append_log(repo_path: Path, line: str) -> None:
    log = repo_path / "log.md"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip() + "\n")


def _pending_dir(repo_path: Path) -> Path:
    d = repo_path / ".kb" / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def set_superseded(repo_path: Path, old_fact: Fact, new_id: str) -> None:
    """Persist supersession to the old fact's markdown so reindex keeps it hidden."""
    if not old_fact.path:
        return
    p = Path(old_fact.path)
    if not p.exists():
        return
    f = markdown_to_fact(p.read_text(), str(p))
    f.superseded_by = new_id
    p.write_text(fact_to_markdown(f))


def write_pending_marker(repo_path: Path, fact_id: str) -> None:
    (_pending_dir(repo_path) / fact_id).write_text("")


def read_pending_markers(repo_path: Path) -> list[str]:
    d = _pending_dir(repo_path)
    return sorted([p.name for p in d.iterdir() if p.is_file()])


def clear_pending_marker(repo_path: Path, fact_id: str) -> None:
    p = _pending_dir(repo_path) / fact_id
    if p.exists():
        p.unlink()
