import json
import re
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
