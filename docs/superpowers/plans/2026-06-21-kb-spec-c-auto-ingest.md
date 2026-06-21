# KB Spec C Implementation Plan — Scriptable + Confidence-Gated Auto-Ingest

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `sources/` → facts automatable: a `kb ingest <file>` CLI that distills a source into atomic facts via claude-proxy with a per-fact confidence gate (≥85 auto-write, <85 → `review/` drafts + `kb review`), plus a nightly ingest phase in `kb consolidate`.

**Architecture:** New `kb/ingest.py` distills via the existing `synth` LLM client and writes facts through `KnowledgeBase.write` (reusing dedup/scope/markdown). Low-confidence facts go to a non-indexed `review/` folder; `kb review accept` promotes survivors. The nightly `kb consolidate` gains an ingest phase (before extract) that auto-ingests opt-in (`kb_scope`-tagged) un-ingested sources.

**Tech Stack:** Python 3.12, claude-proxy via `OpenAIWireClient`, pytest (fake LLM). Docker stack in `~/development/hermes-test`.

## Global Constraints

- **PRIVACY — never commit the real vault path.** `KnotKB` is PUBLIC. Use `${KB_HOST_PATH}` only (gitignored `hermes-test/.env`). `review/` is at `${KB_HOST_PATH}/review/`. Leak-check after every commit. [spec §8]
- **Distillation = one claude-proxy call per source**, model = `KB_INGEST_MODEL or synth_model`; facts written via `KnowledgeBase.write` (dedup/scope/markdown reuse). [spec §2,§4]
- **Scope precedence:** `--scope` > source `kb_scope:` front-matter > `"global"`; `validate_scope` it. [spec §4]
- **Confidence gate:** per-fact `confidence` 0–100; **≥ `KB_INGEST_CONFIDENCE` (85)** auto-writes; **<85** → `review/<id>.md` draft (not indexed). **Missing confidence → 0** (held). [spec §2,§4,§9]
- **Opt-in (nightly):** only sources with a `kb_scope:` directive are auto-ingested. **Idempotent:** `kb_ingested: true` front-matter flag on the source; `--force` re-ingests. [spec §2,§5]
- **Facts only** (no wiki synthesis). [spec §1]
- **`review/` is NOT added to `markdown._INDEXED_DIRS`** — drafts invisible to search/graph until accepted. [spec §3,§6]
- **Nightly ingest phase runs BEFORE the extract phase** in `kb consolidate`. [spec §5]
- Errors (proxy down / bad JSON) → source NOT marked ingested, counted `skipped`, never crash. [spec §4]
- TDD throughout with the fake LLM (`tests/fakes.FakeLLM(reply=...)`); live verify only at Task 5. Baseline suite: **105 passed, 3 skipped**. Branch `feat/spec-c` off master.

## Shared Interfaces (canonical — match exactly)

```python
# kb/config.py — Config gains (frozen dataclass) + from_env:
ingest_enabled: bool = True            # KB_INGEST_ENABLED
ingest_model: str = ""                 # KB_INGEST_MODEL ("" -> synth_model)
ingest_max_sources: int = 10           # KB_INGEST_MAX_SOURCES (per nightly run)
ingest_confidence: int = 85            # KB_INGEST_CONFIDENCE

# kb/ingest.py
def build_ingest_messages(content: str) -> list[dict]
def parse_facts_json(text: str) -> list[dict]          # [{content, tags, confidence}], tolerant; missing confidence -> 0
def read_source_meta(path) -> tuple[dict, str]         # (front-matter dict, body); ({}, text) if no front-matter
def mark_ingested(path) -> None                        # set kb_ingested: true (adds a front-matter block if none)
def write_review_draft(repo_path, scope, content, tags, confidence, source, ts) -> Path   # review/<id>.md, -N on collision
def ingest_file(path, kb, llm, config, scope=None, force=False) -> dict   # {facts_written, facts_held, skipped}
def list_reviews(repo_path) -> list[dict]              # [{path, scope, tags, confidence, source, content}]
def accept_reviews(repo_path, kb, source=None) -> dict # {accepted, skipped, remaining}
def ingest_pending_sources(repo_path, kb, llm, config) -> dict  # {sources_ingested, facts_written, facts_held, skipped}

# kb/store.py — reused unchanged: KnowledgeBase(store, embedder, repo_path, config[, clock, reranker])
#   .write(scope, content, tags=None, source=None) -> dict ; .repo_path ; .clock()
# kb/util.py — reused: validate_scope, content_hash, make_id
# kb/consolidate.py — consolidate(... llm=None): add report["ingested"] + ingest phase BEFORE extract
# kb/cli.py — add `kb ingest <file> [--scope] [--force]` and `kb review [--accept] [--source]`
```

Reused unchanged: `synth.OpenAIWireClient` (LLMClient), `extract.parse_entities_json` (the balanced-bracket parse to mirror), `markdown.fact_to_markdown/markdown_to_fact`, `tests/fakes.FakeLLM/FakeEmbedder/InMemoryVectorStore`.

---

## Task 1: Config knobs + ingest.py parse/meta helpers

**Files:**
- Modify: `kb-mcp/kb/config.py`
- Create: `kb-mcp/kb/ingest.py` (with `build_ingest_messages`, `parse_facts_json`, `read_source_meta`, `mark_ingested`)
- Test: `kb-mcp/tests/test_config.py`, `kb-mcp/tests/test_ingest.py`

**Interfaces:**
- Produces: the four `Config` fields; `build_ingest_messages`, `parse_facts_json`, `read_source_meta`, `mark_ingested` (Shared Interfaces).

- [ ] **Step 1: Write failing tests**
Append to `tests/test_config.py`:
```python
def test_spec_c_defaults():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "x"})
    assert cfg.ingest_enabled is True
    assert cfg.ingest_model == ""
    assert cfg.ingest_max_sources == 10
    assert cfg.ingest_confidence == 85

def test_spec_c_overrides():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "x",
                           "KB_INGEST_ENABLED": "false", "KB_INGEST_MAX_SOURCES": "3",
                           "KB_INGEST_CONFIDENCE": "70"})
    assert cfg.ingest_enabled is False
    assert cfg.ingest_max_sources == 3
    assert cfg.ingest_confidence == 70
```
Create `tests/test_ingest.py`:
```python
from pathlib import Path
from kb.ingest import parse_facts_json, read_source_meta, mark_ingested, build_ingest_messages

def test_build_ingest_messages_has_system_and_content():
    msgs = build_ingest_messages("Anna owns testing.")
    assert msgs[0]["role"] == "system" and "JSON" in msgs[0]["content"]
    assert "Anna owns testing." in msgs[1]["content"]

def test_parse_facts_json_tolerant_and_confidence_defaults():
    out = parse_facts_json('note [{"content":"A","tags":["t"],"confidence":90},'
                           '{"content":"B"}] trailing')
    assert out[0] == {"content": "A", "tags": ["t"], "confidence": 90}
    assert out[1] == {"content": "B", "tags": [], "confidence": 0}   # missing -> 0
    assert parse_facts_json("not json") == []
    assert parse_facts_json('[{"tags":["x"]}]') == []                # no content dropped

def test_parse_facts_json_clamps_confidence():
    assert parse_facts_json('[{"content":"A","confidence":250}]')[0]["confidence"] == 100
    assert parse_facts_json('[{"content":"A","confidence":"bad"}]')[0]["confidence"] == 0

def test_read_source_meta_and_mark_ingested(tmp_path):
    p = tmp_path / "s.md"
    p.write_text("---\nkb_scope: project:flintt\ncreated: 2026-06-21\n---\n\nbody text here")
    meta, body = read_source_meta(str(p))
    assert meta["kb_scope"] == "project:flintt"
    assert body.strip() == "body text here"
    mark_ingested(str(p))
    meta2, _ = read_source_meta(str(p))
    assert meta2["kb_ingested"] is True and meta2["kb_scope"] == "project:flintt"

def test_mark_ingested_adds_frontmatter_when_absent(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("just a plain note, no front-matter")
    mark_ingested(str(p))
    meta, body = read_source_meta(str(p))
    assert meta["kb_ingested"] is True
    assert "just a plain note" in body
```

- [ ] **Step 2: Run — expect FAIL**
Run: `cd ~/development/knowledge-base/kb-mcp && .venv/bin/pytest tests/test_config.py -k spec_c tests/test_ingest.py -v`
Expected: FAIL (missing attrs / `ModuleNotFoundError: kb.ingest`).

- [ ] **Step 3: Add Config fields** (`kb/config.py`)
Add to the dataclass (after `backlink_boost`):
```python
    ingest_enabled: bool = True
    ingest_model: str = ""
    ingest_max_sources: int = 10
    ingest_confidence: int = 85
```
Add to `from_env`'s `Config(...)`:
```python
            ingest_enabled=flag("KB_INGEST_ENABLED", True),
            ingest_model=env.get("KB_INGEST_MODEL", ""),
            ingest_max_sources=int(env.get("KB_INGEST_MAX_SOURCES", "10")),
            ingest_confidence=int(env.get("KB_INGEST_CONFIDENCE", "85")),
```

- [ ] **Step 4: Create `kb/ingest.py`** (helpers only for this task)
```python
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
```

- [ ] **Step 5: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest -q`
Expected: PASS (~111 passed, 3 skipped).

- [ ] **Step 6: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/config.py kb-mcp/kb/ingest.py kb-mcp/tests/test_config.py kb-mcp/tests/test_ingest.py
git commit -m "feat(kb): Spec C config + ingest parse/meta helpers"
```

---

## Task 2: `write_review_draft` + `ingest_file` (confidence split)

**Files:**
- Modify: `kb-mcp/kb/ingest.py`
- Test: `kb-mcp/tests/test_ingest.py`

**Interfaces:**
- Consumes: `KnowledgeBase.write`/`.repo_path`/`.clock`, `synth` LLMClient, `parse_facts_json`, `read_source_meta`, `mark_ingested`, `validate_scope`, `content_hash`, `make_id`.
- Produces: `write_review_draft`, `ingest_file` (Shared Interfaces).

- [ ] **Step 1: Write the failing test**
Append to `tests/test_ingest.py`:
```python
from datetime import datetime, timezone
from kb.config import Config
from kb.store import KnowledgeBase
from kb.ingest import ingest_file, write_review_draft
from tests.fakes import FakeEmbedder, InMemoryVectorStore, FakeLLM

FIXED = datetime(2026, 6, 21, tzinfo=timezone.utc)

def _kb(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED), cfg

def test_ingest_file_splits_by_confidence(tmp_path):
    kb, cfg = _kb(tmp_path)
    src = tmp_path / "sources"; src.mkdir()
    f = src / "note.md"
    f.write_text("---\nkb_scope: project:flintt\n---\n\nAnna owns testing.")
    llm = FakeLLM(reply='[{"content":"Anna owns testing","tags":["qa"],"confidence":92},'
                        '{"content":"Maybe Anna prefers dark mode","tags":[],"confidence":40}]')
    counts = ingest_file(str(f), kb, llm, cfg)
    assert counts == {"facts_written": 1, "facts_held": 1, "skipped": 0}
    # high-confidence fact written under the directive scope
    written = list((tmp_path / "memory" / "project" / "flintt").glob("*.md"))
    assert written and "Anna owns testing" in written[0].read_text()
    # low-confidence fact held in review/, NOT in memory/
    drafts = list((tmp_path / "review").glob("*.md"))
    assert drafts and "dark mode" in drafts[0].read_text()
    # source marked ingested
    from kb.ingest import read_source_meta
    assert read_source_meta(str(f))[0]["kb_ingested"] is True

def test_ingest_file_skips_when_already_ingested(tmp_path):
    kb, cfg = _kb(tmp_path)
    f = tmp_path / "s.md"
    f.write_text("---\nkb_scope: global\nkb_ingested: true\n---\n\nbody")
    llm = FakeLLM(reply='[{"content":"X","confidence":99}]')
    assert ingest_file(str(f), kb, llm, cfg) == {"facts_written": 0, "facts_held": 0, "skipped": 1}
    assert llm.calls == []                          # no LLM call when skipping
    assert ingest_file(str(f), kb, llm, cfg, force=True)["facts_written"] == 1  # --force overrides

def test_ingest_file_scope_precedence(tmp_path):
    kb, cfg = _kb(tmp_path)
    f = tmp_path / "s.md"
    f.write_text("---\nkb_scope: project:flintt\n---\n\nbody")
    llm = FakeLLM(reply='[{"content":"X","confidence":99}]')
    ingest_file(str(f), kb, llm, cfg, scope="global")   # explicit --scope wins
    assert list((tmp_path / "memory" / "global").glob("*.md"))

def test_ingest_file_llm_error_does_not_mark_ingested(tmp_path):
    kb, cfg = _kb(tmp_path)
    f = tmp_path / "s.md"
    f.write_text("---\nkb_scope: global\n---\n\nbody")
    class Boom:
        def complete(self, m, model): raise RuntimeError("proxy down")
    assert ingest_file(str(f), kb, Boom(), cfg) == {"facts_written": 0, "facts_held": 0, "skipped": 1}
    from kb.ingest import read_source_meta
    assert "kb_ingested" not in read_source_meta(str(f))[0]   # left for retry
```

- [ ] **Step 2: Run — expect FAIL** (`ImportError: ingest_file`)
Run: `.venv/bin/pytest tests/test_ingest.py -k "confidence or already_ingested or precedence or llm_error" -v`

- [ ] **Step 3: Add `write_review_draft` + `ingest_file`** to `kb/ingest.py`
```python
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
```

- [ ] **Step 4: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_ingest.py -v && .venv/bin/pytest -q`

- [ ] **Step 5: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/ingest.py kb-mcp/tests/test_ingest.py
git commit -m "feat(kb): ingest_file with confidence split + review drafts"
```

---

## Task 3: Review flow (`list_reviews`/`accept_reviews`) + `kb review` CLI

**Files:**
- Modify: `kb-mcp/kb/ingest.py`, `kb-mcp/kb/cli.py`
- Test: `kb-mcp/tests/test_ingest.py`, `kb-mcp/tests/test_cli.py`

**Interfaces:**
- Consumes: `read_source_meta`, `KnowledgeBase.write`, `validate_scope`.
- Produces: `list_reviews`, `accept_reviews`; `kb review [--accept] [--source]`.

- [ ] **Step 1: Write the failing test**
Append to `tests/test_ingest.py`:
```python
def test_list_and_accept_reviews(tmp_path):
    kb, cfg = _kb(tmp_path)
    from kb.ingest import write_review_draft, list_reviews, accept_reviews
    write_review_draft(tmp_path, "global", "draft fact one", ["t"], 40, "n.md", FIXED)
    write_review_draft(tmp_path, "global", "draft fact two", [], 50, "n.md", FIXED)
    listed = list_reviews(tmp_path)
    assert len(listed) == 2 and any("draft fact one" in d["content"] for d in listed)
    res = accept_reviews(tmp_path, kb)
    assert res["accepted"] == 2
    assert not list((tmp_path / "review").glob("*.md"))           # drafts removed
    assert len(list((tmp_path / "memory" / "global").glob("*.md"))) == 2  # promoted

def test_accept_reviews_skips_invalid_scope(tmp_path):
    kb, cfg = _kb(tmp_path)
    from kb.ingest import write_review_draft, accept_reviews
    write_review_draft(tmp_path, "not a scope", "x", [], 40, "n.md", FIXED)
    res = accept_reviews(tmp_path, kb)
    assert res["accepted"] == 0 and res["skipped"] == 1
    assert list((tmp_path / "review").glob("*.md"))               # draft left in place
```
Append to `tests/test_cli.py`:
```python
def test_review_list_and_accept_cli(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from kb.store import KnowledgeBase
    from kb.ingest import write_review_draft
    from tests.fakes import FakeEmbedder, InMemoryVectorStore
    import datetime as _dt
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder(); store = InMemoryVectorStore(emb)
    write_review_draft(tmp_path, "global", "held fact", ["t"], 40, "n.md",
                       _dt.datetime(2026, 6, 21, tzinfo=_dt.timezone.utc))
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    assert cli.main(["review"]) == 0
    assert "held fact" in capsys.readouterr().out
    assert cli.main(["review", "--accept"]) == 0
    assert "accepted=1" in capsys.readouterr().out
```

- [ ] **Step 2: Run — expect FAIL**
Run: `.venv/bin/pytest tests/test_ingest.py -k "list_and_accept or invalid_scope" tests/test_cli.py -k review -v`

- [ ] **Step 3: Add `list_reviews` + `accept_reviews`** to `kb/ingest.py`
```python
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
```

- [ ] **Step 4: Add the `kb review` subcommand** (`kb/cli.py`)
Add the subparser (after the `extract` parser):
```python
    rev = sub.add_parser("review", help="list or accept low-confidence ingest drafts")
    rev.add_argument("--accept", action="store_true", help="promote drafts into the KB")
    rev.add_argument("--source", help="only accept drafts from this source filename")
```
Add the branch (before `return 1`):
```python
    if args.cmd == "review":
        from kb.ingest import list_reviews, accept_reviews
        from kb.store import KnowledgeBase
        if args.accept:
            kb = KnowledgeBase(store, embedder, cfg.repo_path, cfg)
            r = accept_reviews(cfg.repo_path, kb, source=args.source)
            print(f"accepted={r['accepted']} skipped={r['skipped']} remaining={r['remaining']}")
            return 0
        for d in list_reviews(cfg.repo_path):
            print(f"[{d['confidence']}] {d['source']}: {d['content'][:70]} ({d['path']})")
        return 0
```

- [ ] **Step 5: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_ingest.py tests/test_cli.py -v && .venv/bin/pytest -q`

- [ ] **Step 6: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/ingest.py kb-mcp/kb/cli.py kb-mcp/tests/test_ingest.py kb-mcp/tests/test_cli.py
git commit -m "feat(kb): review queue (list/accept) + kb review CLI"
```

---

## Task 4: `ingest_pending_sources` + nightly phase + `kb ingest` CLI

**Files:**
- Modify: `kb-mcp/kb/ingest.py`, `kb-mcp/kb/consolidate.py`, `kb-mcp/kb/cli.py`
- Test: `kb-mcp/tests/test_ingest.py`, `kb-mcp/tests/test_consolidate.py`, `kb-mcp/tests/test_cli.py`

**Interfaces:**
- Consumes: `ingest_file`, `read_source_meta`, `KnowledgeBase`.
- Produces: `ingest_pending_sources`; `consolidate(... llm=)` runs an ingest phase before extract (`report["ingested"]`); `kb ingest <file> [--scope] [--force]`.

- [ ] **Step 1: Write the failing test**
Append to `tests/test_ingest.py`:
```python
def test_ingest_pending_only_opted_in_and_capped(tmp_path):
    kb, cfg = _kb(tmp_path)
    cfg = Config(repo_path=tmp_path, db_url="x", ingest_max_sources=1)
    src = tmp_path / "sources"; src.mkdir()
    (src / "a.md").write_text("---\nkb_scope: global\n---\n\nfact a")
    (src / "b.md").write_text("---\nkb_scope: global\n---\n\nfact b")
    (src / "ref.md").write_text("just reference material, no kb_scope")  # not opted in
    from kb.ingest import ingest_pending_sources
    res = ingest_pending_sources(tmp_path, kb, FakeLLM(reply='[{"content":"X","confidence":99}]'), cfg)
    assert res["sources_ingested"] == 1                  # cap honored
    # ref.md without kb_scope is never touched
    assert "kb_ingested" not in __import__("kb.ingest", fromlist=["read_source_meta"]).read_source_meta(str(src / "ref.md"))[0]
```
Append to `tests/test_consolidate.py`:
```python
def test_consolidate_runs_ingest_phase_before_extract(tmp_path):
    from kb.models import Fact
    from datetime import datetime, timezone
    from tests.fakes import FakeLLM
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x", extract_enabled=False)  # isolate ingest
    emb = FakeEmbedder()
    src = tmp_path / "sources"; src.mkdir()
    (src / "n.md").write_text("---\nkb_scope: project:flintt\n---\n\nAnna owns testing")
    report = consolidate(store, emb, tmp_path, cfg, apply=True, now=FIXED,
                         llm=FakeLLM(reply='[{"content":"Anna owns testing","confidence":95}]'))
    assert report["ingested"]["sources_ingested"] == 1
    assert list((tmp_path / "memory" / "project" / "flintt").glob("*.md"))
```
Append to `tests/test_cli.py`:
```python
def test_ingest_subcommand(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from tests.fakes import FakeEmbedder, InMemoryVectorStore, FakeLLM
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder(); store = InMemoryVectorStore(emb)
    f = tmp_path / "note.md"; f.write_text("---\nkb_scope: global\n---\n\nbody")
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    monkeypatch.setattr(cli, "_llm", lambda c: FakeLLM(reply='[{"content":"X","confidence":99}]'))
    assert cli.main(["ingest", str(f)]) == 0
    assert "facts_written=1" in capsys.readouterr().out
```

- [ ] **Step 2: Run — expect FAIL**
Run: `.venv/bin/pytest tests/test_ingest.py -k pending tests/test_consolidate.py -k ingest_phase tests/test_cli.py -k ingest_subcommand -v`

- [ ] **Step 3: Add `ingest_pending_sources`** to `kb/ingest.py`
```python
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
```

- [ ] **Step 4: Add the ingest phase to `consolidate`** (`kb/consolidate.py`)
In the `report = {...}` literal, add the `ingested` default key:
```python
    report = {"near_dups": [], "auto_merged": [], "stale": [],
              "orphans": [], "tag_drift": [],
              "ingested": {"sources_ingested": 0, "facts_written": 0, "facts_held": 0, "skipped": 0},
              "extracted": {"facts_extracted": 0, "entities_created": 0, "skipped": 0}}
    if llm is not None and config.ingest_enabled:
        from kb.ingest import ingest_pending_sources
        from kb.store import KnowledgeBase
        kb = KnowledgeBase(store, embedder, repo_path, config)
        report["ingested"] = ingest_pending_sources(repo_path, kb, llm, config)
    if llm is not None and config.extract_enabled:
        from kb.extract import extract_over_facts
        report["extracted"] = extract_over_facts(repo_path, llm, config)
```
(The ingest block goes immediately before the existing extract block, so ingested facts are extracted in the same run.) In `_write_report`, render `ingested` alongside `extracted`:
```python
    lines.append(f"## ingested\n- {report['ingested']}\n")
    lines.append(f"## extracted\n- {report['extracted']}\n")
```
and add to the `append_log` summary string:
```python
               f"ingested={report['ingested']['sources_ingested']} "
```
(place it before the `extracted=` field).

- [ ] **Step 5: Add the `kb ingest` subcommand** (`kb/cli.py`)
Add the subparser (after the `review` parser):
```python
    ing = sub.add_parser("ingest", help="distill a source file into facts (confidence-gated)")
    ing.add_argument("file")
    ing.add_argument("--scope")
    ing.add_argument("--force", action="store_true")
```
Add the branch (before `return 1`):
```python
    if args.cmd == "ingest":
        from kb.ingest import ingest_file
        from kb.store import KnowledgeBase
        kb = KnowledgeBase(store, embedder, cfg.repo_path, cfg)
        c = ingest_file(args.file, kb, _llm(cfg), cfg, scope=args.scope, force=args.force)
        print(f"facts_written={c['facts_written']} facts_held={c['facts_held']} skipped={c['skipped']}")
        return 0
```

- [ ] **Step 6: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_ingest.py tests/test_consolidate.py tests/test_cli.py -v && .venv/bin/pytest -q`

- [ ] **Step 7: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/ingest.py kb-mcp/kb/consolidate.py kb-mcp/kb/cli.py \
        kb-mcp/tests/test_ingest.py kb-mcp/tests/test_consolidate.py kb-mcp/tests/test_cli.py
git commit -m "feat(kb): nightly ingest phase + kb ingest CLI"
```

---

## Task 5: Wiring + live verify

**Files:**
- Modify: `~/development/hermes-test/docker-compose.yml`, `~/development/hermes-test/.env.example`
- Create: `docs/reference/spec-c-ops.md`

**Interfaces:**
- Produces: a deployed stack where `kb ingest`/nightly auto-ingest work with the confidence gate.

- [ ] **Step 1: Add Spec C env to compose** — under the `kb-mcp` `environment:` list in `~/development/hermes-test/docker-compose.yml`:
```yaml
      - KB_INGEST_ENABLED=${KB_INGEST_ENABLED:-true}
      - KB_INGEST_MODEL=${KB_INGEST_MODEL:-}
      - KB_INGEST_MAX_SOURCES=${KB_INGEST_MAX_SOURCES:-10}
      - KB_INGEST_CONFIDENCE=${KB_INGEST_CONFIDENCE:-85}
```
Document the same keys (names + defaults) in `.env.example`.

- [ ] **Step 2: Create `docs/reference/spec-c-ops.md`**
```markdown
# Spec C ops — scriptable + confidence-gated ingest

- `kb ingest <file> [--scope S] [--force]` — distill a source into atomic facts via claude-proxy.
  Facts with confidence >= KB_INGEST_CONFIDENCE (85) are written; lower ones go to `review/`.
- Nightly `kb consolidate --apply` auto-ingests sources that have a `kb_scope:` front-matter
  directive and no `kb_ingested:` flag (capped at KB_INGEST_MAX_SOURCES), before entity extraction.
- `kb review` lists held drafts; `kb review --accept [--source X]` promotes survivors into the KB
  (prune the bad ones in Obsidian first). Drafts live in `review/` and are NOT searched until accepted.
- Opt-in: add `kb_scope: project:<name>` (or `global`) to a source's front-matter. Idempotent via
  `kb_ingested: true`; re-ingest with `--force`. Set `KB_INGEST_ENABLED=false` to disable the nightly phase.
- Note: the interactive `kb-ingest` skill should also set `kb_ingested: true` on sources it processes
  so the nightly CLI doesn't re-ingest them.
```

- [ ] **Step 3: Rebuild + live verify**
```bash
cd ~/development/hermes-test
docker compose --env-file .env up -d --build kb-mcp
for i in $(seq 1 60); do curl -fsS http://127.0.0.1:8077/health >/dev/null 2>&1 && { echo healthy; break; }; sleep 3; done
# create a test source inside the vault with a mix of clear + speculative lines:
docker compose --env-file .env exec -T kb-mcp sh -c 'mkdir -p /kb/sources && printf -- "---\nkb_scope: project:test\n---\n\nThe deploy script lives at scripts/deploy.sh. It probably also emails the team, maybe.\n" > /kb/sources/spec-c-test.md'
docker compose --env-file .env exec -T kb-mcp kb ingest /kb/sources/spec-c-test.md
docker compose --env-file .env exec -T kb-mcp sh -c 'echo MEMORY:; ls /kb/memory/project/test 2>/dev/null; echo REVIEW:; ls /kb/review 2>/dev/null'
docker compose --env-file .env exec -T kb-mcp kb review
```
Expected: `kb ingest` prints `facts_written=… facts_held=…`; the clear fact lands in `/kb/memory/project/test/`, the speculative one in `/kb/review/`; `kb review` lists the held draft. Optionally `kb review --accept` then confirm it moved.

- [ ] **Step 4: Clean up the test artifact** (so it doesn't pollute the real KB)
```bash
cd ~/development/hermes-test
docker compose --env-file .env exec -T kb-mcp sh -c 'rm -f /kb/sources/spec-c-test.md /kb/review/*.md; rm -rf /kb/memory/project/test'
docker compose --env-file .env exec -T kb-mcp kb reindex
```

- [ ] **Step 5: Commit the docs**
```bash
cd ~/development/knowledge-base
git add docs/reference/spec-c-ops.md
git commit -m "docs(kb): Spec C ops (scriptable + confidence-gated ingest)"
```

---

## Self-Review

**1. Spec coverage:**
- §2/§4 distill via claude-proxy, KnowledgeBase.write, scope precedence → Tasks 2 (+config 1). ✓
- §2/§4 confidence gate (≥85 write, <85 review, missing→0) → Tasks 1 (parse default 0), 2 (split). ✓
- §6 review/ not indexed + `kb review list/accept` → Task 3 (and review/ never added to `_INDEXED_DIRS`). ✓
- §2/§5 opt-in via kb_scope, idempotent via kb_ingested, `--force` → Tasks 1 (mark/read), 2 (skip/force), 4 (pending filter). ✓
- §5 nightly ingest phase BEFORE extract + cap → Task 4. ✓
- §4 error handling (proxy down/bad JSON → skip, not marked, no crash) → Tasks 1 (parse []), 2 (try/except), 4 (per-source try). ✓
- §1 facts-only (no wiki) → confirmed (no wiki code anywhere). ✓
- §7 TDD targets (parse, ingest_file split/skip/force/scope/error, review list/accept + invalid scope, pending opt-in/cap, consolidate phase) → Tasks 1-4. ✓
- §8 privacy (${KB_HOST_PATH}, review/ in vault, compose env) → Global Constraints + Tasks 4,5. ✓
- §9 resolved (missing conf→0; mark_ingested adds front-matter; accept re-reads scope + skips invalid; review -N collision; agent-skill note) → Tasks 1,2,3,5. ✓

No gaps.

**2. Placeholder scan:** No TBD/"handle edge cases"/"similar to Task N". Every code step complete. Only non-literal is `${KB_HOST_PATH}` (privacy). ✓

**3. Type consistency:** `Config.ingest_*`, `build_ingest_messages`, `parse_facts_json`→`[{content,tags,confidence}]`, `read_source_meta`→`(dict, str)`, `mark_ingested`, `write_review_draft(repo_path,scope,content,tags,confidence,source,ts)`, `ingest_file(path,kb,llm,config,scope=,force=)`→`{facts_written,facts_held,skipped}`, `list_reviews`→list of `{path,scope,tags,confidence,source,content}`, `accept_reviews(repo_path,kb,source=)`→`{accepted,skipped,remaining}`, `ingest_pending_sources(repo_path,kb,llm,config)`→`{sources_ingested,facts_written,facts_held,skipped}`, `consolidate(... llm=)` with `report["ingested"]`, and reused `KnowledgeBase.write/.repo_path/.clock`, `validate_scope/content_hash/make_id` — all consistent across tasks and matched to the verified current code. ✓
