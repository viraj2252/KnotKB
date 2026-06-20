import json
import re
from pathlib import Path

import yaml

from kb.links import fact_slug
from kb.markdown import read_all_facts, markdown_to_fact, fact_to_markdown
from kb.models import Fact
from kb.util import slugify, content_hash

_ENT_LINE = re.compile(r"(?m)^Entities: .*$")
_SYS = (
    "Extract named entities from the note. Return ONLY a JSON array of objects with keys "
    'name, type, canonical, aliases. "type" must be one of: {types}. Use an existing '
    "canonical name when the entity is already known. Return [] if there are none."
)


def build_extraction_messages(content: str, existing_names: list[str], types) -> list[dict]:
    sys = _SYS.format(types=", ".join(types))
    known = ", ".join(existing_names) if existing_names else "(none)"
    return [{"role": "system", "content": sys},
            {"role": "user", "content": f"Known entities: {known}\n\nNote:\n{content}"}]


def parse_entities_json(text: str, types) -> list[dict]:
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
        name = (e.get("canonical") or e.get("name") or "").strip()
        etype = (e.get("type") or "").strip().lower()
        if not name or etype not in types:
            continue
        aliases = [a for a in (e.get("aliases") or []) if isinstance(a, str)]
        raw_name = (e.get("name") or "").strip()
        if raw_name and raw_name != name:
            aliases.append(raw_name)
        out.append({"name": name, "type": etype, "aliases": sorted(set(aliases))})
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def load_entities(repo_path) -> dict:
    base = Path(repo_path) / "entities"
    ents = {}
    if base.exists():
        for p in sorted(base.glob("*.md")):
            f = markdown_to_fact(p.read_text(), str(p))
            ents[fact_slug(f)] = f
    return ents


def write_entity_page(repo_path, slug: str, etype: str, aliases: list[str], summary: str) -> None:
    d = Path(repo_path) / "entities"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"type": etype, "slug": slug, "aliases": sorted(set(aliases))}
    front = yaml.safe_dump(meta, sort_keys=True, default_flow_style=False).strip()
    (d / f"{slug}.md").write_text(f"---\n{front}\n---\n\n# {summary}\n")


def upsert_entity(repo_path, name: str, etype: str, aliases: list[str], existing: dict) -> str:
    repo_path = Path(repo_path)
    norms = {_norm(name)} | {_norm(a) for a in aliases}
    # topic that matches an existing wiki page reuses that page (no entities/ node)
    if etype == "topic" and (repo_path / "wiki" / f"{slugify(name)}.md").exists():
        return slugify(name)
    # dedup against existing entity pages by slug/alias
    for slug, f in existing.items():
        cand = {_norm(slug)} | {_norm(a) for a in (f.aliases or [])}
        if norms & cand:
            merged = sorted(set((f.aliases or []) + aliases + [name]) - {slug})
            write_entity_page(repo_path, slug, f.entity_type or etype, merged, f"{(f.entity_type or etype).title()}: {name}")
            existing[slug] = markdown_to_fact((repo_path / "entities" / f"{slug}.md").read_text(),
                                              str(repo_path / "entities" / f"{slug}.md"))
            return slug
    # new entity, with -N collision suffix
    base = slugify(name)
    slug, n = base, 2
    while (repo_path / "entities" / f"{slug}.md").exists():
        slug = f"{base}-{n}"
        n += 1
    write_entity_page(repo_path, slug, etype, sorted(set(aliases)), f"{etype.title()}: {name}")
    existing[slug] = markdown_to_fact((repo_path / "entities" / f"{slug}.md").read_text(),
                                      str(repo_path / "entities" / f"{slug}.md"))
    return slug


def cache_and_link(fact: Fact, slugs: list[str]) -> None:
    fact.entities = list(slugs)
    fact.extracted = True
    body = _ENT_LINE.sub("", fact.content).rstrip()
    if slugs:
        body += "\n\nEntities: " + ", ".join(f"[[{s}]]" for s in slugs)
    fact.content = body
    fact.content_hash = content_hash(body)
    if fact.path:
        Path(fact.path).write_text(fact_to_markdown(fact))


def extract_over_facts(repo_path, llm, config) -> dict:
    repo_path = Path(repo_path)
    facts = read_all_facts(repo_path, include_sources=False)
    todo = [f for f in facts if f.entity_type is None and not f.extracted and f.path]
    todo = todo[: config.extract_max_facts]
    existing = load_entities(repo_path)
    start = len(existing)
    counts = {"facts_extracted": 0, "entities_created": 0, "skipped": 0}
    model = config.extract_model or config.synth_model
    for f in todo:
        msgs = build_extraction_messages(f.content, list(existing.keys()), config.entity_types)
        try:
            raw = llm.complete(msgs, model)
        except Exception:
            counts["skipped"] += 1
            continue
        slugs = [upsert_entity(repo_path, e["name"], e["type"], e["aliases"], existing)
                 for e in parse_entities_json(raw, config.entity_types)]
        cache_and_link(f, slugs)
        counts["facts_extracted"] += 1
    counts["entities_created"] = len(load_entities(repo_path)) - start
    return counts
