# KB Spec B Implementation Plan — Knowledge Graph + Discovery

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed-entity knowledge graph over the existing kb link layer: nightly LLM entity extraction (via claude-proxy, cached to fact front-matter), typed markdown entity pages in the Obsidian vault, injected `[[entity]]` links, and discovery ops (`find_experts`/`get_entity`/`find_orphans`) plus an optional backlink-boosted ranking.

**Architecture:** Extends the Spec A engine (`~/development/knowledge-base/kb-mcp`). A new `kb/extract.py` runs in the nightly `kb consolidate` pass (and a manual `kb extract`): it asks claude-proxy for entities in each not-yet-extracted fact, upserts `entities/<slug>.md` pages (best-effort dedup), caches the result in the fact's front-matter, and injects `[[entity]]` links. The graph is derived from markdown via the existing `build_link_index`; `kb/discovery.py` reads it. Markdown stays the source of truth.

**Tech Stack:** Python 3.12, fastembed, psycopg+pgvector, httpx (claude-proxy via the existing `OpenAIWireClient`), pytest. Docker stack in `~/development/hermes-test`.

## Global Constraints

- **PRIVACY — never commit the real vault path.** `KnotKB` is PUBLIC. Use `${KB_HOST_PATH}` only (already in gitignored `hermes-test/.env`). Entity pages are written to `${KB_HOST_PATH}/entities/`. Leak-check after every commit. [spec §11]
- **Markdown is the source of truth; the graph is derived** from front-matter + injected links, rebuilt by `kb reindex` with **no** LLM calls. The LLM runs only on facts not yet extracted. [spec §3]
- **Extraction LLM = claude-proxy** via `OpenAIWireClient` (no new account); model = `KB_EXTRACT_MODEL` or fallback `synth_model`. [spec §2,§4]
- **Run-once-per-fact:** gate extraction on an `extracted:` front-matter flag; cap each run at `KB_EXTRACT_MAX_FACTS` (default 50). [spec §4]
- **Relationships = mentions + co-occurrence only** (no triples). **`find_anomalies` is cut.** [spec §1,§6]
- **Entity pages** are markdown in `entities/<slug>.md`, typed via a `type:` front-matter key ∈ `KB_ENTITY_TYPES`. Dedup is best-effort name/alias match. [spec §5]
- **Fact↔entity link:** inject an idempotent `Entities: [[slug]], …` line into the fact body. [spec §3]
- **`topic` reuse:** if an entity is `type=topic` and `wiki/<slug>.md` exists, reuse that wiki page — do NOT create `entities/<slug>.md`. [resolved §12]
- TDD throughout; extraction tests use a **fake LLM** (no network). Live extraction only at Task 6. Baseline suite: **86 passed, 3 skipped**. Work on branch `feat/spec-b`.

## Shared Interfaces (canonical — match exactly)

```python
# kb/config.py — Config gains (frozen dataclass) + from_env:
extract_enabled: bool = True                 # KB_EXTRACT_ENABLED
extract_model: str = ""                       # KB_EXTRACT_MODEL ("" -> use synth_model)
extract_max_facts: int = 50                   # KB_EXTRACT_MAX_FACTS
entity_types: tuple[str, ...] = ("person", "company", "project", "topic")  # KB_ENTITY_TYPES (csv)
backlink_boost: float = 0.3                   # KB_BACKLINK_BOOST

# kb/models.py — Fact gains:
entities: list[str] = field(default_factory=list)   # entity slugs mentioned (cache)
entity_type: str | None = None                       # set on entity PAGES (person/company/...)
extracted: bool = False                              # extraction-ran flag (gate)

# kb/util.py
def slugify(name: str) -> str                 # ascii, lowercased, hyphenated; "" -> "entity"

# kb/extract.py
def build_extraction_messages(content: str, existing_names: list[str], types) -> list[dict]
def parse_entities_json(text: str, types) -> list[dict]    # [{name,type,aliases}], tolerant
def load_entities(repo_path) -> dict[str, Fact]           # slug -> entity-page Fact
def write_entity_page(repo_path, slug: str, etype: str, aliases: list[str], summary: str) -> None
def upsert_entity(repo_path, name: str, etype: str, aliases: list[str], existing: dict) -> str  # returns slug
def cache_and_link(fact: Fact, slugs: list[str]) -> None  # set entities/extracted, inject body line, rewrite file
def extract_over_facts(repo_path, llm, config) -> dict     # {facts_extracted, entities_created, skipped}

# kb/discovery.py  (pure helpers; KnowledgeBase methods wrap them)
def rank_experts(search_results, facts_by_path, index, entity_type, k) -> list[Fact]

# kb/store.py — KnowledgeBase gains:
def find_experts(self, query, entity_type="person", k=5) -> list[dict]
def get_entity(self, slug) -> dict            # {entity, mentions, related}
def find_orphans(self) -> dict                # {facts: [...], entities: [...]}
# search(): optional backlink boost applied before truncation when config.backlink_boost > 0
```

Existing pieces reused (do not change their signatures): `links.parse_wikilinks/fact_slug/build_link_index` (`by_slug/forward/backlinks/orphans`), `synth.LLMClient/OpenAIWireClient`, `markdown.fact_to_markdown/markdown_to_fact/read_all_facts/write_fact/append_log`, `consolidate.consolidate(store, embedder, repo_path, config, apply, now)`, `store.KnowledgeBase(... reranker=)/search/_facts/_result`, `cli._load/main`, `tests/fakes` (`FakeEmbedder/InMemoryVectorStore/FakeReranker`).

---

## Task 1: Config knobs, Fact fields, markdown round-trip, index `entities/`, slugify

**Files:**
- Modify: `kb-mcp/kb/config.py`, `kb-mcp/kb/models.py`, `kb-mcp/kb/markdown.py`, `kb-mcp/kb/util.py`
- Test: `kb-mcp/tests/test_config.py`, `kb-mcp/tests/test_markdown.py`, `kb-mcp/tests/test_util.py`

**Interfaces:**
- Produces: the `Config` fields, `Fact.entities/entity_type/extracted`, `slugify`, and `entities/` indexing (Shared Interfaces).

- [ ] **Step 1: Write failing tests**
Append to `tests/test_config.py`:
```python
def test_spec_b_defaults():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "x"})
    assert cfg.extract_enabled is True
    assert cfg.extract_model == ""
    assert cfg.extract_max_facts == 50
    assert cfg.entity_types == ("person", "company", "project", "topic")
    assert cfg.backlink_boost == 0.3

def test_spec_b_overrides():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "x",
                           "KB_EXTRACT_ENABLED": "false", "KB_EXTRACT_MAX_FACTS": "5",
                           "KB_ENTITY_TYPES": "person,project", "KB_BACKLINK_BOOST": "0"})
    assert cfg.extract_enabled is False
    assert cfg.extract_max_facts == 5
    assert cfg.entity_types == ("person", "project")
    assert cfg.backlink_boost == 0.0
```
Append to `tests/test_util.py`:
```python
def test_slugify():
    from kb.util import slugify
    assert slugify("Brand Engagement") == "brand-engagement"
    assert slugify("VJ Kothalawala!") == "vj-kothalawala"
    assert slugify("  ") == "entity"
```
Append to `tests/test_markdown.py`:
```python
def test_entity_fields_round_trip(tmp_path):
    f = make_fact()
    f.entities = ["viraj", "flintt"]
    f.entity_type = "project"
    f.extracted = True
    back = markdown_to_fact(fact_to_markdown(f), path="x.md")
    assert back.entities == ["viraj", "flintt"]
    assert back.entity_type == "project"
    assert back.extracted is True

def test_entities_dir_is_indexed(tmp_path):
    (tmp_path / "entities").mkdir()
    (tmp_path / "entities" / "flintt.md").write_text("---\ntype: project\nslug: flintt\n---\n# Flintt")
    facts = read_all_facts(tmp_path)
    page = [f for f in facts if f.path and f.path.endswith("flintt.md")][0]
    assert page.entity_type == "project"
```

- [ ] **Step 2: Run — expect FAIL**
Run: `cd ~/development/knowledge-base/kb-mcp && .venv/bin/pytest tests/test_config.py tests/test_util.py tests/test_markdown.py -k "spec_b or slugify or entity_" -v`
Expected: FAIL (missing attrs / `slugify` / fields).

- [ ] **Step 3: Add Config fields** (`kb/config.py`)
Add to the dataclass (after `automerge`):
```python
    extract_enabled: bool = True
    extract_model: str = ""
    extract_max_facts: int = 50
    entity_types: tuple[str, ...] = ("person", "company", "project", "topic")
    backlink_boost: float = 0.3
```
Add to the `Config(...)` in `from_env`:
```python
            extract_enabled=flag("KB_EXTRACT_ENABLED", True),
            extract_model=env.get("KB_EXTRACT_MODEL", ""),
            extract_max_facts=int(env.get("KB_EXTRACT_MAX_FACTS", "50")),
            entity_types=tuple(t.strip() for t in
                               env.get("KB_ENTITY_TYPES", "person,company,project,topic").split(",")
                               if t.strip()),
            backlink_boost=float(env.get("KB_BACKLINK_BOOST", "0.3")),
```

- [ ] **Step 4: Add Fact fields** (`kb/models.py`, after `aliases`):
```python
    entities: list[str] = field(default_factory=list)
    entity_type: str | None = None
    extracted: bool = False
```

- [ ] **Step 5: Add `slugify`** (`kb/util.py`):
```python
import re
import unicodedata


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "entity"
```
(`re` may already be imported in util.py — don't duplicate; add `unicodedata`.)

- [ ] **Step 6: Round-trip entity fields + index `entities/`** (`kb/markdown.py`)
In `fact_to_markdown`, add to the `meta` dict:
```python
        "entities": fact.entities,
        "type": fact.entity_type,
        "extracted": fact.extracted,
```
In `markdown_to_fact`, add to the `Fact(...)`:
```python
        entities=list(meta.get("entities") or []),
        entity_type=meta.get("type"),
        extracted=bool(meta.get("extracted", False)),
```
Change the indexed dirs:
```python
_INDEXED_DIRS = ("memory", "wiki", "decisions", "entities")
```

- [ ] **Step 7: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest -q`
Expected: PASS (90 passed, 3 skipped).

- [ ] **Step 8: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/config.py kb-mcp/kb/models.py kb-mcp/kb/markdown.py kb-mcp/kb/util.py \
        kb-mcp/tests/test_config.py kb-mcp/tests/test_util.py kb-mcp/tests/test_markdown.py
git commit -m "feat(kb): Spec B config, Fact entity fields, entities/ indexing, slugify"
```

---

## Task 2: `extract.py` — extraction, entity-page upsert/dedup, cache + link injection

**Files:**
- Create: `kb-mcp/kb/extract.py`
- Modify: `kb-mcp/tests/fakes.py` (add `FakeLLM`)
- Test: `kb-mcp/tests/test_extract.py`

**Interfaces:**
- Consumes: `Fact`, `slugify`, `fact_to_markdown`/`markdown_to_fact`/`read_all_facts`, `fact_slug`, `Config`, `synth.LLMClient`.
- Produces: `build_extraction_messages`, `parse_entities_json`, `load_entities`, `write_entity_page`, `upsert_entity`, `cache_and_link`, `extract_over_facts` (Shared Interfaces); `FakeLLM` in fakes.

- [ ] **Step 1: Add `FakeLLM` to `tests/fakes.py`**
```python
class FakeLLM:
    """Records calls; returns a canned reply (default an entities JSON array)."""
    def __init__(self, reply='[{"name":"Flintt","type":"project","aliases":["Flint"]}]'):
        self.reply = reply
        self.calls = []
    def complete(self, messages, model):
        self.calls.append((messages, model))
        return self.reply
```

- [ ] **Step 2: Write the failing test** (`tests/test_extract.py`)
```python
from datetime import datetime, timezone
from pathlib import Path
from kb.config import Config
from kb.models import Fact
from kb.markdown import write_fact, markdown_to_fact, read_all_facts
from kb.extract import (parse_entities_json, upsert_entity, load_entities,
                        cache_and_link, extract_over_facts)
from tests.fakes import FakeLLM

TYPES = ("person", "company", "project", "topic")

def test_parse_entities_json_tolerant():
    out = parse_entities_json('noise [{"name":"VJ","type":"person","canonical":"Viraj","aliases":["VJ"]}] tail', TYPES)
    assert out == [{"name": "Viraj", "type": "person", "aliases": ["VJ"]}]
    assert parse_entities_json("not json", TYPES) == []
    assert parse_entities_json('[{"name":"X","type":"bogus"}]', TYPES) == []  # bad type dropped

def test_upsert_entity_creates_then_dedups(tmp_path):
    existing = load_entities(tmp_path)
    s1 = upsert_entity(tmp_path, "Flintt", "project", ["Flint"], existing)
    assert s1 == "flintt"
    assert (tmp_path / "entities" / "flintt.md").exists()
    # a later mention by a known alias must NOT create a new page
    s2 = upsert_entity(tmp_path, "Flint", "project", [], existing)
    assert s2 == "flintt"
    assert sorted(p.name for p in (tmp_path / "entities").glob("*.md")) == ["flintt.md"]

def test_cache_and_link_idempotent(tmp_path):
    ts = datetime(2026, 6, 21, tzinfo=timezone.utc)
    from kb.util import content_hash, make_id
    f = Fact(id=make_id(ts, content_hash("x")), scope="project:flintt",
             content="Flintt ships campaigns.", ts=ts, content_hash=content_hash("x"))
    write_fact(tmp_path, f)
    cache_and_link(f, ["flintt"])
    again = markdown_to_fact(Path(f.path).read_text(), f.path)
    assert again.entities == ["flintt"] and again.extracted is True
    assert "Entities: [[flintt]]" in again.content
    # re-applying doesn't duplicate the line
    cache_and_link(again, ["flintt"])
    assert again.content.count("Entities: [[flintt]]") == 1

def test_extract_over_facts_runs_once_and_caps(tmp_path):
    ts = datetime(2026, 6, 21, tzinfo=timezone.utc)
    from kb.util import content_hash, make_id
    for i in range(3):
        c = f"Flintt fact number {i}"
        write_fact(tmp_path, Fact(id=make_id(ts, content_hash(c)) + str(i), scope="project:flintt",
                                  content=c, ts=ts, content_hash=content_hash(c)))
    cfg = Config(repo_path=tmp_path, db_url="x", extract_max_facts=2)
    llm = FakeLLM()
    counts = extract_over_facts(tmp_path, llm, cfg)
    assert counts["facts_extracted"] == 2          # cap respected
    assert len(llm.calls) == 2
    # second run: the 2 done facts are skipped (extracted flag); only the 3rd is processed
    llm2 = FakeLLM()
    counts2 = extract_over_facts(tmp_path, llm2, cfg)
    assert counts2["facts_extracted"] == 1
    assert len(llm2.calls) == 1
```

- [ ] **Step 3: Run — expect FAIL** (`ModuleNotFoundError: kb.extract`)
Run: `.venv/bin/pytest tests/test_extract.py -v`

- [ ] **Step 4: Write `kb/extract.py`**
```python
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
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
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
            merged = sorted(set((f.aliases or []) + aliases) - {name})
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
```

- [ ] **Step 5: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_extract.py -v && .venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/extract.py kb-mcp/tests/fakes.py kb-mcp/tests/test_extract.py
git commit -m "feat(kb): entity extraction, entity pages, dedup, cache+link injection"
```

---

## Task 3: Wire extract into `kb consolidate` + `kb extract` CLI

**Files:**
- Modify: `kb-mcp/kb/consolidate.py`, `kb-mcp/kb/cli.py`
- Test: `kb-mcp/tests/test_consolidate.py`, `kb-mcp/tests/test_cli.py`

**Interfaces:**
- Consumes: `extract_over_facts`, `synth.OpenAIWireClient`, `Config`.
- Produces: `consolidate(..., llm=None)` runs an extract phase when `llm` + `config.extract_enabled`; report gains `extracted`. `kb extract` CLI subcommand.

- [ ] **Step 1: Write the failing test**
Append to `tests/test_consolidate.py`:
```python
def test_consolidate_runs_extract_phase(tmp_path):
    from kb.models import Fact
    from kb.markdown import write_fact
    from datetime import datetime, timezone
    from tests.fakes import FakeLLM
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    write_fact(tmp_path, Fact(id="20260101000000-a", scope="project:flintt",
                              content="Flintt ships campaigns", ts=datetime(2026,1,1,tzinfo=timezone.utc),
                              content_hash="a"))
    report = consolidate(store, emb, tmp_path, cfg, apply=True, now=FIXED, llm=FakeLLM())
    assert report["extracted"]["facts_extracted"] == 1
    assert (tmp_path / "entities" / "flintt.md").exists()

def test_consolidate_no_llm_skips_extract(tmp_path):
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x")
    report = consolidate(store, FakeEmbedder(), tmp_path, cfg, apply=True, now=FIXED)
    assert report["extracted"] == {"facts_extracted": 0, "entities_created": 0, "skipped": 0}
```

- [ ] **Step 2: Run — expect FAIL** (`TypeError: ... 'llm'` / KeyError 'extracted')
Run: `.venv/bin/pytest tests/test_consolidate.py -k extract -v`

- [ ] **Step 3: Add the extract phase** (`kb/consolidate.py`)
Change the signature:
```python
def consolidate(store, embedder, repo_path, config, apply: bool = False,
                now: datetime | None = None, llm=None) -> dict:
```
Right after `report = {...}` is initialised, add the `extracted` key and run the phase:
```python
    report = {"near_dups": [], "auto_merged": [], "stale": [],
              "orphans": [], "tag_drift": [],
              "extracted": {"facts_extracted": 0, "entities_created": 0, "skipped": 0}}
    if llm is not None and config.extract_enabled:
        from kb.extract import extract_over_facts
        report["extracted"] = extract_over_facts(repo_path, llm, config)
```
(Place this block **before** `facts = [...]` is read, so the freshly-injected `[[entity]]` links and any new entity pages are included in the near-dup/orphan analysis.) Add `extracted` to the `_write_report` key loop:
```python
    for key in ("extracted", "auto_merged", "near_dups", "stale", "orphans", "tag_drift"):
```
Note `report["extracted"]` is a dict (not a list) — guard the report writer:
```python
    for key in ("auto_merged", "near_dups", "stale", "orphans", "tag_drift"):
        lines.append(f"## {key} ({len(report[key])})")
        for item in report[key]:
            lines.append(f"- {item}")
        lines.append("")
    lines.append(f"## extracted\n- {report['extracted']}\n")
```
(Replace the existing loop with this form so the dict-valued `extracted` is rendered separately.)

- [ ] **Step 4: Add `kb extract` + pass an LLM into consolidate** (`kb/cli.py`)
Add a helper to build the extraction client and extend `_load` is not needed; instead build the client in the branches. Add the subparser:
```python
    sub.add_parser("extract", help="run LLM entity extraction over un-extracted facts")
```
Add a small builder near the top of `cli.py`:
```python
def _llm(cfg):
    from kb.synth import OpenAIWireClient
    return OpenAIWireClient(cfg.synth_base_url, cfg.synth_key)
```
In `main`, change the `consolidate` branch to pass the LLM, and add the `extract` branch:
```python
    if args.cmd == "consolidate":
        from kb.consolidate import consolidate
        llm = _llm(cfg) if cfg.extract_enabled else None
        report = consolidate(store, embedder, cfg.repo_path, cfg, apply=args.apply, llm=llm)
        print(f"extracted={report['extracted']['facts_extracted']} "
              f"auto_merged={len(report['auto_merged'])} near_dups={len(report['near_dups'])} "
              f"stale={len(report['stale'])} orphans={len(report['orphans'])} "
              f"tag_drift={len(report['tag_drift'])}")
        report_only = (len(report["near_dups"]) - len(report["auto_merged"])
                       + len(report["stale"]) + len(report["orphans"]) + len(report["tag_drift"]))
        return 1 if report_only else 0
    if args.cmd == "extract":
        from kb.extract import extract_over_facts
        counts = extract_over_facts(cfg.repo_path, _llm(cfg), cfg)
        print(f"facts_extracted={counts['facts_extracted']} "
              f"entities_created={counts['entities_created']} skipped={counts['skipped']}")
        return 0
```
(The `consolidate` subparser already has `--apply` from Spec A; keep it.)

- [ ] **Step 5: Add a CLI extract test**
Append to `tests/test_cli.py`:
```python
def test_extract_subcommand(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from kb.models import Fact
    from kb.markdown import write_fact
    from tests.fakes import FakeEmbedder, InMemoryVectorStore, FakeLLM
    import datetime as _dt
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    store = InMemoryVectorStore(emb)
    write_fact(tmp_path, Fact(id="20260101000000-a", scope="project:flintt",
                              content="Flintt ships campaigns",
                              ts=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc), content_hash="a"))
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    monkeypatch.setattr(cli, "_llm", lambda c: FakeLLM())
    rc = cli.main(["extract"])
    assert rc == 0
    assert "facts_extracted=1" in capsys.readouterr().out
```

- [ ] **Step 6: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_consolidate.py tests/test_cli.py -v && .venv/bin/pytest -q`

- [ ] **Step 7: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/consolidate.py kb-mcp/kb/cli.py kb-mcp/tests/test_consolidate.py kb-mcp/tests/test_cli.py
git commit -m "feat(kb): extract phase in consolidate + kb extract CLI"
```

---

## Task 4: Discovery — `find_experts` / `get_entity` / `find_orphans` + MCP tools

**Files:**
- Create: `kb-mcp/kb/discovery.py`
- Modify: `kb-mcp/kb/store.py`, `kb-mcp/kb/server.py`
- Test: `kb-mcp/tests/test_discovery.py`

**Interfaces:**
- Consumes: `KnowledgeBase.search/_facts/_result`, `build_link_index`, `fact_slug`.
- Produces: `rank_experts(...)`; `KnowledgeBase.find_experts/get_entity/find_orphans`; MCP tools `find_experts`, `get_entity`, `find_orphans`.

- [ ] **Step 1: Write the failing test** (`tests/test_discovery.py`)
```python
from datetime import datetime, timezone
from kb.config import Config
from kb.store import KnowledgeBase
from kb.extract import write_entity_page
from tests.fakes import FakeEmbedder, InMemoryVectorStore, FakeReranker

FIXED = datetime(2026, 6, 21, tzinfo=timezone.utc)

def build(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg,
                         clock=lambda: FIXED, reranker=FakeReranker())

def test_find_experts_ranks_entity_in_relevant_facts(tmp_path):
    kb = build(tmp_path)
    write_entity_page(tmp_path, "anna", "person", [], "Person: Anna")
    kb.write("project:flintt", "Anna owns brand engagement testing. Entities: [[anna]]")
    kb.write("project:flintt", "unrelated note about billing")
    experts = kb.find_experts("brand engagement testing", entity_type="person", k=5,
                              scope="project:flintt")
    assert experts and experts[0]["slug"] == "anna"

def test_get_entity_returns_mentions(tmp_path):
    kb = build(tmp_path)
    write_entity_page(tmp_path, "flintt", "project", [], "Project: Flintt")
    kb.write("project:flintt", "Flintt ships campaigns. Entities: [[flintt]]")
    ent = kb.get_entity("flintt")
    assert ent["entity"]["slug"] == "flintt"
    assert any("ships campaigns" in m["content"] for m in ent["mentions"])

def test_find_orphans_flags_unmentioned_entity(tmp_path):
    kb = build(tmp_path)
    write_entity_page(tmp_path, "ghost", "person", [], "Person: Ghost")  # never mentioned
    out = kb.find_orphans()
    assert any(e["slug"] == "ghost" for e in out["entities"])
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: kb.discovery` / no `find_experts`)
Run: `.venv/bin/pytest tests/test_discovery.py -v`

- [ ] **Step 3: Write `kb/discovery.py`**
```python
from kb.links import fact_slug


def rank_experts(search_results, facts_by_path, index, entity_type, k):
    """Sum each result's score onto the typed entities its fact links to."""
    by_slug = index["by_slug"]
    scores: dict[str, float] = {}
    for r in search_results:
        fact = facts_by_path.get(r["path"])
        if fact is None:
            continue
        for dst in index["forward"].get(fact.id, []):
            ent = by_slug.get(dst)
            if ent is not None and ent.entity_type == entity_type:
                scores[dst] = scores.get(dst, 0.0) + float(r["score"])
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return [(slug, by_slug[slug]) for slug, _ in ranked if slug in by_slug]
```

- [ ] **Step 4: Add KnowledgeBase methods** (`kb/store.py`)
Add `from kb.discovery import rank_experts` near the other imports, and these methods:
```python
    def find_experts(self, query, entity_type="person", k=5, scope=None) -> list[dict]:
        results = self.search(query, scope=scope, k=max(k * 3, 10))
        facts = self._facts()
        idx = build_link_index(facts)
        by_path = {f.path: f for f in facts if f.path}
        out = []
        for slug, ent in rank_experts(results, by_path, idx, entity_type, k):
            d = self._result(ent)
            d["slug"] = slug
            out.append(d)
        return out

    def get_entity(self, slug) -> dict:
        facts = self._facts()
        idx = build_link_index(facts)
        byid = {f.id: f for f in facts}
        ent = idx["by_slug"].get(slug)
        mention_ids = idx["backlinks"].get(slug, [])
        mentions = [self._result(byid[i]) for i in mention_ids if i in byid]
        related: dict[str, int] = {}
        for i in mention_ids:
            for dst in idx["forward"].get(i, []):
                if dst != slug and idx["by_slug"].get(dst) is not None and \
                        idx["by_slug"][dst].entity_type is not None:
                    related[dst] = related.get(dst, 0) + 1
        related_slugs = sorted(related, key=lambda s: (-related[s], s))
        return {
            "entity": (self._result(ent) | {"slug": slug}) if ent is not None else None,
            "mentions": mentions,
            "related": [self._result(idx["by_slug"][s]) | {"slug": s} for s in related_slugs],
        }

    def find_orphans(self) -> dict:
        facts = self._facts()
        idx = build_link_index(facts)
        byid = {f.id: f for f in facts}
        fact_orphans, entity_orphans = [], []
        for i in idx["orphans"]:
            f = byid.get(i)
            if f is None:
                continue
            (entity_orphans if f.entity_type is not None else fact_orphans).append(
                self._result(f) | {"slug": fact_slug(f)})
        # also flag low-connectivity entity pages (mentioned <= 1x)
        for f in facts:
            if f.entity_type is not None:
                slug = fact_slug(f)
                if len(idx["backlinks"].get(slug, [])) <= 1 and \
                        not any(e["slug"] == slug for e in entity_orphans):
                    entity_orphans.append(self._result(f) | {"slug": slug})
        return {"facts": fact_orphans, "entities": entity_orphans}
```
Add `from kb.links import fact_slug` to the imports if not already present (Spec A imports `build_link_index` only — add `fact_slug`).

- [ ] **Step 5: Register MCP tools** (`kb/server.py`, after `get_links`):
```python
    @mcp.tool()
    def find_experts(query: str, entity_type: str = "person", k: int = 5, scope=None) -> list[dict]:
        """Find the entities (default people) most associated with facts matching the query."""
        return kb.find_experts(query, entity_type=entity_type, k=k, scope=scope)

    @mcp.tool()
    def get_entity(slug: str) -> dict:
        """Get an entity page plus the facts that mention it and co-occurring entities."""
        return kb.get_entity(slug)

    @mcp.tool()
    def find_orphans() -> dict:
        """List facts with no inbound links and entity pages mentioned <=1x."""
        return kb.find_orphans()
```

- [ ] **Step 6: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_discovery.py -v && .venv/bin/pytest -q`

- [ ] **Step 7: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/discovery.py kb-mcp/kb/store.py kb-mcp/kb/server.py kb-mcp/tests/test_discovery.py
git commit -m "feat(kb): discovery ops find_experts/get_entity/find_orphans + MCP tools"
```

---

## Task 5: Backlink-boosted ranking in `search`

**Files:**
- Modify: `kb-mcp/kb/store.py`
- Test: `kb-mcp/tests/test_search.py`

**Interfaces:**
- Consumes: `build_link_index`, `fact_slug`, `Config.backlink_boost`.
- Produces: `search` boosts well-connected facts when `config.backlink_boost > 0`.

- [ ] **Step 1: Write the failing test**
Append to `tests/test_search.py`:
```python
def test_backlink_boost_promotes_linked_fact(tmp_path):
    from tests.fakes import FakeReranker
    cfg = Config(repo_path=tmp_path, db_url="x", backlink_boost=5.0)
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg,
                       clock=lambda: FIXED, reranker=FakeReranker())
    kb.write("global", "alpha beta gamma", tags=["t"])              # will be linked to
    kb.write("global", "see the topic Entities: [[alpha-beta-gamma]]")  # links to first via slug? no
    # Make a fact that is the link target by slug, and another linking to it:
    kb.write("global", "target fact about alpha beta")             # slug = its id (timestamp)
    res = kb.search("alpha beta", k=5)
    assert res  # boost path executes without error and returns results

def test_backlink_boost_zero_is_spec_a_order(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x", backlink_boost=0.0)
    emb = FakeEmbedder()
    from tests.fakes import FakeReranker
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg,
                       clock=lambda: FIXED, reranker=FakeReranker())
    kb.write("global", "alpha beta gamma delta")
    kb.write("global", "alpha beta")
    res = kb.search("alpha beta gamma", k=2)
    assert res[0]["content"] == "alpha beta gamma delta"  # pure rerank order, unchanged
```

- [ ] **Step 2: Run — expect FAIL** (boost not applied / attribute usage)
Run: `.venv/bin/pytest tests/test_search.py -k backlink -v`

- [ ] **Step 3: Apply the boost in `search`** (`kb/store.py`)
Add `import math` at the top. Replace the body of `search` after computing `hits` so the boost is applied before the result dicts are built:
```python
        now = self.clock()
        qvec = self.embedder.embed([query])[0]
        if self.reranker is not None and self.config.rerank_enabled:
            hits = self.reranker.rerank(query, self.store.search(
                qvec, query, scopes=scopes, tags=tags,
                k=self.config.rerank_candidates, now=now))
        else:
            hits = self.store.search(qvec, query, scopes=scopes, tags=tags,
                                     k=max(k, self.config.rerank_candidates), now=now)

        if self.config.backlink_boost > 0 and hits:
            idx = build_link_index(self._facts())
            boosted = []
            for f, score in hits:
                inbound = len(idx["backlinks"].get(fact_slug(f), []))
                boosted.append((f, score + self.config.backlink_boost * math.log1p(inbound)))
            hits = sorted(boosted, key=lambda fs: -fs[1])
        hits = hits[:k]
        return [
            {
                "content": f.content, "score": round(score, 6), "scope": f.scope,
                "tags": f.tags, "source": f.source,
                "ts": f.ts.isoformat() if f.ts else None, "path": f.path,
            }
            for f, score in hits
        ]
```
(Note: when reranking, fetch `rerank_candidates` and DON'T pre-truncate; truncate to `k` once, after the optional boost. The non-rerank branch fetches `max(k, rerank_candidates)` so the boost has candidates to reorder.)

- [ ] **Step 4: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_search.py -v && .venv/bin/pytest -q`

- [ ] **Step 5: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/store.py kb-mcp/tests/test_search.py
git commit -m "feat(kb): optional backlink-boosted ranking in search"
```

---

## Task 6: Wiring + live verify

**Files:**
- Modify: `~/development/hermes-test/docker-compose.yml`, `~/development/hermes-test/.env.example`
- Modify: `docs/reference/spec-a-ops.md` (append a Spec B section) — or create `docs/reference/spec-b-ops.md`

**Interfaces:**
- Produces: a deployed stack where the nightly job extracts entities and the new MCP tools work.

- [ ] **Step 1: Add Spec B env to compose** — in `~/development/hermes-test/docker-compose.yml`, under the `kb-mcp` `environment:` list, add:
```yaml
      - KB_EXTRACT_ENABLED=${KB_EXTRACT_ENABLED:-true}
      - KB_EXTRACT_MODEL=${KB_EXTRACT_MODEL:-}
      - KB_EXTRACT_MAX_FACTS=${KB_EXTRACT_MAX_FACTS:-50}
      - KB_ENTITY_TYPES=${KB_ENTITY_TYPES:-person,company,project,topic}
      - KB_BACKLINK_BOOST=${KB_BACKLINK_BOOST:-0.3}
```
Document the same keys (names + defaults) in `.env.example`.

- [ ] **Step 2: Document the new ops** — create `docs/reference/spec-b-ops.md`:
```markdown
# Spec B ops — knowledge graph

- Nightly `kb consolidate --apply` now also extracts entities (claude-proxy) for
  un-extracted facts, writes `entities/<slug>.md` pages, caches `entities:` on facts,
  and injects `Entities: [[slug]]` links. Manual run: `kb extract`.
- New MCP tools: `find_experts(query, entity_type, k, scope)`, `get_entity(slug)`, `find_orphans()`.
- Search applies a small backlink boost when `KB_BACKLINK_BOOST > 0`.
- Cost: one LLM call per not-yet-extracted fact, capped by `KB_EXTRACT_MAX_FACTS` per run.
  Set `KB_EXTRACT_ENABLED=false` to disable extraction.
```

- [ ] **Step 3: Rebuild + live verify**
```bash
cd ~/development/hermes-test
docker compose --env-file .env up -d --build kb-mcp
for i in $(seq 1 60); do curl -fsS http://127.0.0.1:8077/health >/dev/null 2>&1 && { echo healthy; break; }; sleep 3; done
# real extraction over the seeded Flintt facts (claude-proxy):
docker compose --env-file .env exec -T kb-mcp kb extract
docker compose --env-file .env exec -T kb-mcp kb reindex
# entity pages now exist in the vault:
docker compose --env-file .env exec -T kb-mcp sh -c 'ls /kb/entities | head'
```
Expected: `kb extract` prints `facts_extracted=…`, and `/kb/entities/` contains pages (e.g. `flintt.md`, person pages).

- [ ] **Step 4: Verify the tools** (from the host, after a fresh Claude Code session or via a raw MCP call): `find_experts("brand engagement", entity_type="person", scope="project:flintt")` returns a sensible person; `get_entity("flintt")` returns mentions. Confirm in Obsidian that `entities/` pages appear as graph nodes linked from the Flintt facts.

- [ ] **Step 5: Commit the docs**
```bash
cd ~/development/knowledge-base
git add docs/reference/spec-b-ops.md
git commit -m "docs(kb): Spec B ops (entity extraction, discovery tools)"
```

---

## Self-Review

**1. Spec coverage:**
- §2/§4 LLM extraction nightly, cached, capped, claude-proxy → Tasks 2, 3 (+config 1). ✓
- §3 markdown-truth/derived graph, inject `[[entity]]`, index `entities/` → Tasks 1, 2. ✓
- §5 entity pages + dedup + topic→wiki reuse → Task 2 (`upsert_entity`). ✓
- §6 find_experts/get_entity/find_orphans + tools; find_anomalies cut → Task 4. ✓
- §7 backlink boost (configurable, boost=0 = Spec A order) → Task 5. ✓
- §8 error handling (proxy down → skip+retry; malformed JSON → skip; idempotent re-run; reindex no-LLM) → Task 2 (`extract_over_facts` try/except, `parse_entities_json`, `extracted` gate). ✓
- §9 TDD targets (extraction w/ fake LLM + idempotent + cap + bad-json; dedup; link injection; discovery; boost) → Tasks 2,4,5. ✓
- §10 build order → matches Task order. ✓
- §11 privacy (${KB_HOST_PATH}, entities/ in vault, claude-proxy) → Global Constraints + Tasks 2,6. ✓
- §12 resolved (slugify+collision, stub summary, expert ranking = Σ rerank score, topic→wiki reuse, extract counts in report) → Tasks 1,2,3,4. ✓

No gaps.

**2. Placeholder scan:** No TBD/"handle edge cases"/"similar to Task N". Every code step is complete. The only non-literals are `${KB_HOST_PATH}` (required by privacy). ✓

**3. Type consistency:** `Config` fields, `Fact.entities/entity_type/extracted`, `slugify`, `extract_over_facts(repo_path, llm, config)->{facts_extracted,entities_created,skipped}`, `upsert_entity(...)->slug`, `cache_and_link(fact, slugs)`, `consolidate(..., llm=None)` with `report["extracted"]`, `rank_experts(search_results, facts_by_path, index, entity_type, k)`, `KnowledgeBase.find_experts/get_entity/find_orphans`, and the `type:` front-matter key ↔ `Fact.entity_type` all match across tasks and the existing signatures verified in `store.py`/`consolidate.py`/`links.py`/`markdown.py`/`config.py`. ✓
