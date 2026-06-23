import json
from pathlib import Path

import yaml

from kb.util import validate_scope, content_hash, make_id

_SYS = (
    "Distill the note into atomic, standalone facts. Return ONLY a JSON array of objects "
    'with keys: content (a single self-contained fact), tags (list of short topic tags), '
    "and confidence (0-100, how clearly the fact is stated in the source). Skip speculation "
    "and meta-commentary. Return [] if there are no durable facts."
)


def build_ingest_messages(content: str) -> list[dict]:
    return [{"role": "system", "content": _SYS},
            {"role": "user", "content": content}]


def parse_facts_json(text: str) -> list[dict]:
    if not text:
        return []
    start = text.find("[")
    if start == -1:
        return []
    depth, end = 0, -1
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return []
    try:
        data = json.loads(text[start:end])
    except Exception:
        return []
    out = []
    for e in data if isinstance(data, list) else []:
        if not isinstance(e, dict):
            continue
        c = (e.get("content") or "").strip()
        if not c:
            continue
        tags = [t for t in (e.get("tags") or []) if isinstance(t, str)]
        try:
            conf = int(e.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0
        out.append({"content": c, "tags": tags, "confidence": max(0, min(100, conf))})
    return out


def read_source_meta(path) -> tuple[dict, str]:
    text = Path(path).read_text()
    if not text.lstrip().startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    return meta, parts[2].lstrip("\n")


def mark_ingested(path) -> None:
    p = Path(path)
    text = p.read_text()
    if text.lstrip().startswith("---") and len(text.split("---", 2)) >= 3:
        _, front, body = text.split("---", 2)
        meta = yaml.safe_load(front) or {}
        meta["kb_ingested"] = True
        dumped = yaml.safe_dump(meta, sort_keys=True, default_flow_style=False).strip()
        p.write_text(f"---\n{dumped}\n---{body}")
    else:
        p.write_text(f"---\nkb_ingested: true\n---\n\n{text}")


def write_review_draft(repo_path, scope, content, tags, confidence, source, ts) -> Path:
    d = Path(repo_path) / "review"
    d.mkdir(parents=True, exist_ok=True)
    base = make_id(ts, content_hash(content))
    slug, n = base, 2
    while (d / f"{slug}.md").exists():
        slug = f"{base}-{n}"
        n += 1
    meta = {"scope": scope, "tags": list(tags), "confidence": confidence, "source": source}
    front = yaml.safe_dump(meta, sort_keys=True, default_flow_style=False).strip()
    p = d / f"{slug}.md"
    p.write_text(f"---\n{front}\n---\n\n{content}\n")
    return p


def list_reviews(repo_path) -> list[dict]:
    d = Path(repo_path) / "review"
    out = []
    if d.exists():
        for p in sorted(d.glob("*.md")):
            meta, body = read_source_meta(str(p))
            out.append({"path": str(p), "scope": meta.get("scope", "global"),
                        "tags": list(meta.get("tags") or []),
                        "confidence": meta.get("confidence"),
                        "source": meta.get("source"), "content": body.strip()})
    return out


def accept_reviews(repo_path, kb, source=None) -> dict:
    res = {"accepted": 0, "skipped": 0, "remaining": 0}
    for d in list_reviews(repo_path):
        if source is not None and d["source"] != source:
            res["remaining"] += 1
            continue
        try:
            validate_scope(d["scope"])
        except ValueError:
            res["skipped"] += 1
            res["remaining"] += 1
            continue
        kb.write(d["scope"], d["content"], tags=d["tags"], source=d["source"])
        Path(d["path"]).unlink()
        res["accepted"] += 1
    return res


def ingest_pending_sources(repo_path, kb, llm, config) -> dict:
    base = Path(repo_path) / "sources"
    res = {"sources_ingested": 0, "facts_written": 0, "facts_held": 0, "skipped": 0}
    if not base.exists():
        return res
    pending = []
    for p in sorted(base.glob("*.md")):
        meta, _ = read_source_meta(str(p))
        if meta.get("kb_scope") and not meta.get("kb_ingested"):
            pending.append(p)
    for p in pending[: config.ingest_max_sources]:
        try:
            c = ingest_file(str(p), kb, llm, config)
        except Exception:
            res["skipped"] += 1
            continue
        res["facts_written"] += c["facts_written"]
        res["facts_held"] += c["facts_held"]
        res["skipped"] += c["skipped"]
        if c["skipped"] == 0:
            res["sources_ingested"] += 1
    return res


def ingest_file(path, kb, llm, config, scope=None, force=False) -> dict:
    meta, body = read_source_meta(path)
    if meta.get("kb_ingested") and not force:
        return {"facts_written": 0, "facts_held": 0, "skipped": 1}
    resolved = scope or meta.get("kb_scope") or "global"
    validate_scope(resolved)
    try:
        raw = llm.complete(build_ingest_messages(body), config.ingest_model or config.synth_model)
    except Exception:
        return {"facts_written": 0, "facts_held": 0, "skipped": 1}
    written = held = 0
    ts = kb.clock()
    src = Path(path).name
    for fact in parse_facts_json(raw):
        if fact["confidence"] >= config.ingest_confidence:
            kb.write(resolved, fact["content"], tags=fact["tags"], source=src)
            written += 1
        else:
            write_review_draft(kb.repo_path, resolved, fact["content"], fact["tags"],
                               fact["confidence"], src, ts)
            held += 1
    mark_ingested(path)
    return {"facts_written": written, "facts_held": held, "skipped": 0}
