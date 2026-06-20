# KB Spec A Implementation Plan — Synthesis, Reranking, Wikilinks, Consolidation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four curated GBrain-style capabilities to the existing `kb` MCP knowledge base — a cited `ask` synthesis tool, local cross-encoder reranking, `[[wikilink]]` backlinks (Obsidian-friendly), and a nightly consolidation pass — after first decoupling the real KB out of the public code repo into the user's Obsidian vault.

**Architecture:** The `kb` engine is Python under `~/development/knowledge-base/kb-mcp` (markdown source of truth + pgvector hybrid + RRF, behind an MCP server + CLI). New focused modules `rerank.py`, `links.py`, `synth.py`, `consolidate.py` plug into the existing `KnowledgeBase`/`server.py`/`cli.py`. Phase 0 moves real content into the Drive-synced Obsidian vault and scrubs the public repo.

**Tech Stack:** Python 3.12, fastembed (embeddings + cross-encoder rerank), psycopg + pgvector, httpx (synthesis client), pytest. Docker Compose (the `kb-mcp`/`kb-postgres` services live in `~/development/hermes-test/docker-compose.yml`).

## Global Constraints

- **PRIVACY — never commit the real vault path.** The `KnotKB` repo (`github.com/viraj2252/KnotKB`) is PUBLIC. The user's Obsidian vault path is personal (contains their email). In every committed file (plan, spec, code, compose, docs) use the env var **`${KB_HOST_PATH}`** only. The real value is set in the **gitignored** `~/development/hermes-test/.env` and exported in the executor's shell as `$KB_HOST_PATH` for Phase 0 commands. It is `<the user's Obsidian vault>/agent-kb` (supplied out-of-band).
- **Real KB lives in the Obsidian vault**, not the code repo. Code repo keeps only an `example/` sample vault; content dirs are gitignored. [spec §11]
- **Markdown is the source of truth; pgvector is derived/rebuildable** (`kb reindex`). The DB volume is local-only, never in the vault. [spec §11]
- **Synthesis LLM = claude-proxy** (`http://claude-proxy:8000/v1`), configurable; no new API account. [spec §2,§4]
- **Reranker = local** fastembed cross-encoder `BAAI/bge-reranker-base`, configurable; no API. [spec §2,§5]
- **Consolidation auto-applies ONLY near-duplicate merges** at cosine ≥ `KB_AUTOMERGE` (default 0.97); staleness/orphans/tag-drift are report-only. Safety net = **supersede-not-delete** (old file retained, flagged). [spec §2,§7]
- **No wikilink ranking boost in Spec A** (deferred to Spec B). [spec §6]
- TDD throughout: failing test → minimal code → pass → commit. The kb venv is at `kb-mcp/.venv`; run tests from `kb-mcp/`. Current baseline: **59 passed, 3 skipped**.

## Shared Interfaces (canonical — match these exactly)

```python
# kb/config.py — Config gains these fields (all with defaults) + from_env wiring
rerank_enabled: bool = True            # KB_RERANK_ENABLED
rerank_model: str = "BAAI/bge-reranker-base"   # KB_RERANK_MODEL
rerank_candidates: int = 30            # KB_RERANK_CANDIDATES
synth_base_url: str = "http://claude-proxy:8000/v1"   # KB_SYNTH_BASE_URL
synth_model: str = "claude-sonnet-4-6" # KB_SYNTH_MODEL
synth_key: str = ""                    # KB_SYNTH_KEY
synth_max_facts: int = 8               # KB_SYNTH_MAX_FACTS
stale_days: int = 180                  # KB_STALE_DAYS
automerge: float = 0.97                # KB_AUTOMERGE

# kb/models.py — Fact gains:
slug: str | None = None
aliases: list[str] = field(default_factory=list)

# kb/rerank.py
class Reranker(Protocol):
    def rerank(self, query: str, candidates: list[tuple[Fact, float]]) -> list[tuple[Fact, float]]: ...
class FastReranker:                     # wraps fastembed cross-encoder
    def __init__(self, model: str = "BAAI/bge-reranker-base") -> None: ...

# kb/links.py
def parse_wikilinks(text: str) -> list[str]
def fact_slug(fact: Fact) -> str        # fact.slug or path stem or id
def build_link_index(facts: list[Fact]) -> dict
    # {"by_slug": {slug: Fact}, "forward": {src_id: [dst_slug]},
    #  "backlinks": {slug: [src_id]}, "orphans": [fact_id]}

# kb/synth.py
class LLMClient(Protocol):
    def complete(self, messages: list[dict], model: str) -> str: ...
def build_messages(question: str, facts: list[Fact]) -> list[dict]
def parse_citations(answer: str, facts: list[Fact]) -> list[dict]   # [{n, path, scope}]
def synthesize(kb, question: str, llm: LLMClient, scope=None, k: int|None=None) -> dict
    # {answer, citations, used_facts} | {error} | insufficient-evidence

# kb/consolidate.py
def consolidate(store, embedder, repo_path, config, apply: bool = False,
                now: datetime | None = None) -> dict   # the report

# kb/store.py — KnowledgeBase.__init__ gains: reranker: Reranker | None = None
#   .search uses it; new methods: get_links(slug), get_backlinks(slug), orphans()
```

---

# PHASE 0 — Decouple content into the Obsidian vault + scrub the public repo

> Ops phase (no pytest). Each task ends with a concrete verification. **Run Phase 0 before any feature task and before using the KB for real.**
>
> **Executor setup (do once, do NOT commit):**
> ```bash
> export KB_HOST_PATH="<the user's Obsidian vault>/agent-kb"   # real path supplied out-of-band
> echo "$KB_HOST_PATH"   # sanity: should print a path ending in /agent-kb
> ```

## Task 1: Migrate real content into the vault's `agent-kb/`

**Files:** none committed — creates files under `$KB_HOST_PATH` (outside any repo).

- [ ] **Step 1: Create the agent-kb structure in the vault**
```bash
mkdir -p "$KB_HOST_PATH"/memory/global "$KB_HOST_PATH"/memory/project \
         "$KB_HOST_PATH"/wiki "$KB_HOST_PATH"/decisions "$KB_HOST_PATH"/sources \
         "$KB_HOST_PATH"/context
```

- [ ] **Step 2: Move current repo content into the vault**
```bash
cd ~/development/knowledge-base
cp -R context/. "$KB_HOST_PATH"/context/ 2>/dev/null || true
cp -R wiki/.    "$KB_HOST_PATH"/wiki/    2>/dev/null || true
cp -R memory/.  "$KB_HOST_PATH"/memory/  2>/dev/null || true
cp -R decisions/. "$KB_HOST_PATH"/decisions/ 2>/dev/null || true
cp -R sources/. "$KB_HOST_PATH"/sources/ 2>/dev/null || true
[ -f log.md ]   && cp log.md   "$KB_HOST_PATH"/log.md
[ -f index.md ] && cp index.md "$KB_HOST_PATH"/index.md
```

- [ ] **Step 3: Verify**
```bash
ls -R "$KB_HOST_PATH" | head -40
test -f "$KB_HOST_PATH"/context/about-me.md && echo "OK: identity moved"
```
Expected: the `agent-kb` tree exists with `context/about-me.md`.

## Task 2: Strip content from the code repo + add an example vault

**Files:**
- Modify: `~/development/knowledge-base/.gitignore`
- Delete (from git): `context/ memory/ wiki/ decisions/ sources/ log.md index.md kb-discussion.md`
- Create: `example/` sample vault (non-personal)

- [ ] **Step 1: Append ignore rules**
Append to `.gitignore`:
```gitignore
# Real KB content never lives in this public repo — it lives in the Obsidian vault.
/context/
/memory/
/wiki/
/decisions/
/sources/
/log.md
/index.md
/kb-discussion.md
.obsidian/
```

- [ ] **Step 2: Remove content from the working tree + git index**
```bash
cd ~/development/knowledge-base
git rm -r --cached context memory wiki decisions sources log.md index.md kb-discussion.md 2>/dev/null || true
rm -rf context memory wiki decisions sources log.md index.md   # local copies now live in the vault
```

- [ ] **Step 3: Create a non-personal `example/` vault**
```bash
mkdir -p example/memory/global example/wiki example/decisions example/sources
```
Write `example/README.md`:
```markdown
# Example agent-kb vault

This mirrors the structure the `kb` server expects at `$KB_HOST_PATH`
(set in hermes-test/.env). Real knowledge lives in your Obsidian vault, NOT here.

- `memory/global/`, `memory/project/<name>/` — atomic facts (one .md per fact, YAML frontmatter)
- `wiki/` — curated topic pages (human filenames = slugs, link with [[wiki-slug]])
- `decisions/` — dated, append-only
- `sources/` — raw transcripts (not indexed by default)
- `log.md`, `index.md`
```
Write `example/wiki/ai-trends.md`:
```markdown
---
slug: ai-trends
tags: [ai-trends]
---

# AI trends

Sample curated topic page. Link to it from a fact with [[ai-trends]].
```
Write `example/memory/global/20260101000000-example.md`:
```markdown
---
id: 20260101000000-example
scope: global
tags: [example]
source: example
ts: '2026-01-01T00:00:00+00:00'
content_hash: ''
superseded_by: null
expires_at: null
slug: null
aliases: []
---

Sample atomic fact. See the [[ai-trends]] page.
```

- [ ] **Step 4: Commit**
```bash
cd ~/development/knowledge-base
git add .gitignore example
git add -u   # stage the removals
git commit -m "chore: move real KB content to the Obsidian vault; keep example only"
```

- [ ] **Step 5: Verify nothing personal remains tracked**
```bash
git ls-files | grep -E '^(context|memory|wiki|decisions|sources)/|^log.md|^index.md|^kb-discussion.md' && echo "STILL TRACKED (bad)" || echo "OK: content untracked"
git ls-files example | head
```
Expected: `OK: content untracked`; `example/...` listed.

## Task 3: Repoint Claude Code @import + compose mount + env; drop obsolete mirror

**Files:**
- Modify: `~/.claude/CLAUDE.md`
- Modify: `~/development/hermes-test/docker-compose.yml`
- Modify: `~/development/hermes-test/.env` and `.env.example`
- Delete: `~/development/knowledge-base/scripts/mirror-to-drive.sh`, `docs/reference/dev.kb.mirror.plist.snapshot`, `docs/reference/durability.md` (obsolete: real KB now syncs via Obsidian↔Drive)

- [ ] **Step 1: Repoint the @import in `~/.claude/CLAUDE.md`**
Replace the last line `@~/development/knowledge-base/context/about-me.md` with an import from the vault. Because `@import` needs a literal path (no env expansion), this single line necessarily contains the real path — but `~/.claude/CLAUDE.md` is OUTSIDE the public repo, so it's fine. Set it to:
```
@<the user's Obsidian vault>/agent-kb/context/about-me.md
```
(Use the real expanded path. Do NOT copy this line into any repo file.)

- [ ] **Step 2: Switch the kb-mcp bind mount to the vault**
In `~/development/hermes-test/docker-compose.yml`, replace the `kb-mcp` `volumes:` block:
```yaml
    volumes:
      - ./memory:/kb/memory
      - ./wiki:/kb/wiki
      - ./decisions:/kb/decisions
      - ./sources:/kb/sources
      - ./log.md:/kb/log.md
```
with a single mount of the vault's agent-kb via env:
```yaml
    volumes:
      - ${KB_HOST_PATH:?set KB_HOST_PATH to your Obsidian vault agent-kb dir}:/kb
```

- [ ] **Step 3: Add `KB_HOST_PATH` to env files**
In `~/development/hermes-test/.env` add (real value):
```bash
KB_HOST_PATH=<the user's Obsidian vault>/agent-kb
```
In `~/development/hermes-test/.env.example` add (placeholder only — this file is committed):
```bash
# Absolute path to your Obsidian vault's agent-kb subfolder (the real KB content).
# Keep the real value only in .env (gitignored), never commit it.
KB_HOST_PATH=
```

- [ ] **Step 4: Remove obsolete durability artifacts**
```bash
cd ~/development/knowledge-base
git rm scripts/mirror-to-drive.sh docs/reference/dev.kb.mirror.plist.snapshot docs/reference/durability.md 2>/dev/null || true
git commit -m "chore: drop repo->Drive mirror; real KB durability is Obsidian<->Drive"
```

- [ ] **Step 5: Verify compose resolves with the vault mount**
```bash
cd ~/development/hermes-test
KB_HOST_PATH="$KB_HOST_PATH" docker compose --env-file .env config -q && echo "compose OK"
test -f "$(grep '^KB_HOST_PATH=' .env | cut -d= -f2-)/context/about-me.md" && echo "OK: vault content reachable"
```
Expected: `compose OK` and `OK: vault content reachable`.

## Task 4: Scrub the committed content from git history + force-push  ⚠️ IRREVERSIBLE / OUTWARD-FACING

> **STOP — confirm with the user before this task.** It rewrites public history and force-pushes. The user explicitly approved a history scrub.

**Files:** rewrites `~/development/knowledge-base` history; updates `origin`.

- [ ] **Step 1: Ensure `git-filter-repo` is available**
```bash
git filter-repo --version 2>/dev/null || pipx install git-filter-repo || pip install git-filter-repo
```

- [ ] **Step 2: Purge content paths from ALL history**
```bash
cd ~/development/knowledge-base
git filter-repo --force --invert-paths \
  --path context --path memory --path wiki --path decisions --path sources \
  --path log.md --path index.md --path kb-discussion.md
```

- [ ] **Step 3: Re-add origin (filter-repo strips remotes) and force-push**
```bash
cd ~/development/knowledge-base
git remote add origin https://github.com/viraj2252/KnotKB.git
git push origin --force --all
git push origin --force --tags
```

- [ ] **Step 4: Verify content is gone from history**
```bash
cd ~/development/knowledge-base
git log --all --oneline -- context memory wiki decisions sources log.md index.md kb-discussion.md | head
```
Expected: **no output** (no commit touches those paths anymore).

## Task 5: Bring the stack up against the vault + verify

**Files:** none.

- [ ] **Step 1: Recreate kb-mcp with the new mount**
```bash
cd ~/development/hermes-test
make up
for i in $(seq 1 60); do curl -fsS http://127.0.0.1:8077/health >/dev/null 2>&1 && { echo healthy; break; }; sleep 3; done
```
Expected: `healthy`.

- [ ] **Step 2: Reindex from the vault + confirm it sees vault content**
```bash
cd ~/development/hermes-test
docker compose --env-file .env exec -T kb-mcp kb reindex
docker compose --env-file .env exec -T kb-mcp sh -c 'ls /kb/context /kb/wiki'
```
Expected: `indexed N facts` (N ≥ the vault's pages) and the vault dirs listed inside the container.

- [ ] **Step 3: Commit a ledger note (optional) and finish Phase 0**
Phase 0 done: real KB in the vault, repo scrubbed, stack reads the vault.

---

# FEATURES

## Task 6: Config knobs for the four features

**Files:**
- Modify: `kb-mcp/kb/config.py`
- Test: `kb-mcp/tests/test_config.py`

**Interfaces:**
- Produces: the `Config` fields in Shared Interfaces (rerank/synth/stale/automerge).

- [ ] **Step 1: Write the failing test**
Append to `tests/test_config.py`:
```python
def test_spec_a_defaults():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "postgresql://x"})
    assert cfg.rerank_enabled is True
    assert cfg.rerank_model == "BAAI/bge-reranker-base"
    assert cfg.rerank_candidates == 30
    assert cfg.synth_base_url == "http://claude-proxy:8000/v1"
    assert cfg.synth_model == "claude-sonnet-4-6"
    assert cfg.synth_key == ""
    assert cfg.synth_max_facts == 8
    assert cfg.stale_days == 180
    assert cfg.automerge == 0.97

def test_spec_a_overrides():
    cfg = Config.from_env({
        "KB_REPO_PATH": "/kb", "KB_DB_URL": "postgresql://x",
        "KB_RERANK_ENABLED": "false", "KB_RERANK_CANDIDATES": "10",
        "KB_SYNTH_MODEL": "claude-opus-4-8", "KB_AUTOMERGE": "0.95",
        "KB_STALE_DAYS": "30",
    })
    assert cfg.rerank_enabled is False
    assert cfg.rerank_candidates == 10
    assert cfg.synth_model == "claude-opus-4-8"
    assert cfg.automerge == 0.95
    assert cfg.stale_days == 30
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: ... 'rerank_enabled'`)
Run: `.venv/bin/pytest tests/test_config.py -v`

- [ ] **Step 3: Add the fields + from_env wiring**
In `kb/config.py`, add to the dataclass (after `index_sources`):
```python
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-base"
    rerank_candidates: int = 30
    synth_base_url: str = "http://claude-proxy:8000/v1"
    synth_model: str = "claude-sonnet-4-6"
    synth_key: str = ""
    synth_max_facts: int = 8
    stale_days: int = 180
    automerge: float = 0.97
```
And in `from_env(...)`, add to the `Config(...)` call:
```python
            rerank_enabled=flag("KB_RERANK_ENABLED", True),
            rerank_model=env.get("KB_RERANK_MODEL", "BAAI/bge-reranker-base"),
            rerank_candidates=int(env.get("KB_RERANK_CANDIDATES", "30")),
            synth_base_url=env.get("KB_SYNTH_BASE_URL", "http://claude-proxy:8000/v1"),
            synth_model=env.get("KB_SYNTH_MODEL", "claude-sonnet-4-6"),
            synth_key=env.get("KB_SYNTH_KEY", ""),
            synth_max_facts=int(env.get("KB_SYNTH_MAX_FACTS", "8")),
            stale_days=int(env.get("KB_STALE_DAYS", "180")),
            automerge=float(env.get("KB_AUTOMERGE", "0.97")),
```

- [ ] **Step 4: Run — expect PASS**
Run: `.venv/bin/pytest tests/test_config.py -v`

- [ ] **Step 5: Commit**
```bash
git add kb-mcp/kb/config.py kb-mcp/tests/test_config.py
git commit -m "feat(kb): config knobs for rerank/synth/consolidation"
```

## Task 7: Reranker module + fake

**Files:**
- Create: `kb-mcp/kb/rerank.py`
- Modify: `kb-mcp/tests/fakes.py` (add `FakeReranker`)
- Test: `kb-mcp/tests/test_rerank.py`

**Interfaces:**
- Consumes: `Fact`.
- Produces: `Reranker` protocol, `FastReranker`, `FakeReranker` (Shared Interfaces).

- [ ] **Step 1: Write the failing test**
`tests/test_rerank.py`:
```python
from datetime import datetime, timezone
from kb.models import Fact
from tests.fakes import FakeReranker

def f(fid, content):
    return Fact(id=fid, scope="global", content=content,
                ts=datetime(2026, 1, 1, tzinfo=timezone.utc))

def test_fake_reranker_orders_by_query_overlap():
    cands = [(f("a", "alpha beta"), 0.1),
             (f("b", "alpha beta gamma delta"), 0.1),
             (f("c", "zeta"), 0.9)]
    out = FakeReranker().rerank("alpha beta gamma", cands)
    assert [fact.id for fact, _ in out] == ["b", "a", "c"]  # most query-overlap first
    assert all(isinstance(s, float) for _, s in out)

def test_fake_reranker_empty():
    assert FakeReranker().rerank("q", []) == []
```

- [ ] **Step 2: Run — expect FAIL** (`ImportError: FakeReranker`)
Run: `.venv/bin/pytest tests/test_rerank.py -v`

- [ ] **Step 3: Write `kb/rerank.py`**
```python
from typing import Protocol

from kb.models import Fact


class Reranker(Protocol):
    def rerank(self, query: str,
               candidates: list[tuple[Fact, float]]) -> list[tuple[Fact, float]]: ...


class FastReranker:
    """Local cross-encoder reranker via fastembed (ONNX CPU, no API)."""

    def __init__(self, model: str = "BAAI/bge-reranker-base") -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # lazy
        self._model = TextCrossEncoder(model_name=model)

    def rerank(self, query, candidates):
        if not candidates:
            return []
        docs = [fact.content for fact, _ in candidates]
        scores = list(self._model.rerank(query, docs))
        ranked = sorted(zip(candidates, scores),
                        key=lambda cs: (-cs[1], cs[0][0].id))
        return [(fact, float(score)) for (fact, _old), score in ranked]
```

- [ ] **Step 4: Add `FakeReranker` to `tests/fakes.py`**
```python
class FakeReranker:
    """Deterministic reranker for tests: scores by query-token overlap."""

    def rerank(self, query, candidates):
        q = set(query.lower().split())
        scored = [(fact, float(len(q & set(fact.content.lower().split()))))
                  for fact, _ in candidates]
        scored.sort(key=lambda fs: (-fs[1], fs[0].id))
        return scored
```

- [ ] **Step 5: Run — expect PASS**
Run: `.venv/bin/pytest tests/test_rerank.py -v`

- [ ] **Step 6: Commit**
```bash
git add kb-mcp/kb/rerank.py kb-mcp/tests/fakes.py kb-mcp/tests/test_rerank.py
git commit -m "feat(kb): cross-encoder reranker (FastReranker) + FakeReranker"
```

## Task 8: Wire reranking into `KnowledgeBase.search`

**Files:**
- Modify: `kb-mcp/kb/store.py`
- Test: `kb-mcp/tests/test_search.py`

**Interfaces:**
- Consumes: `Reranker`, `Config.rerank_enabled/rerank_candidates`.
- Produces: `KnowledgeBase(..., reranker=None)`; `search` reranks when a reranker is set and enabled.

- [ ] **Step 1: Write the failing test**
Append to `tests/test_search.py`:
```python
def test_search_uses_reranker_when_present(tmp_path):
    from tests.fakes import FakeReranker
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg,
                       clock=lambda: FIXED, reranker=FakeReranker())
    kb.write("global", "alpha beta gamma delta")   # most overlap with query
    kb.write("global", "alpha unrelated")
    kb.write("global", "totally other words")
    results = kb.search("alpha beta gamma", k=3)
    assert results[0]["content"] == "alpha beta gamma delta"

def test_search_without_reranker_unchanged(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)
    kb.write("global", "alpha beta")
    assert kb.search("alpha beta")  # still returns results (RRF order)
```

- [ ] **Step 2: Run — expect FAIL** (`TypeError: ... unexpected keyword 'reranker'`)
Run: `.venv/bin/pytest tests/test_search.py -v`

- [ ] **Step 3: Modify `KnowledgeBase`**
In `kb/store.py`, add the import near the top guard:
```python
if TYPE_CHECKING:
    from kb.db import VectorStore
    from kb.rerank import Reranker
```
Change `__init__` signature + body:
```python
    def __init__(self, store: VectorStore, embedder: Embedder, repo_path,
                 config: Config, clock: Callable[[], datetime] = _utcnow,
                 reranker: "Reranker | None" = None) -> None:
        self.store = store
        self.embedder = embedder
        self.repo_path = repo_path
        self.config = config
        self.clock = clock
        self.reranker = reranker
        self._dedup = DedupConfig(config.dedup_merge, config.dedup_skip)
```
In `search`, replace the retrieval line:
```python
        now = self.clock()
        qvec = self.embedder.embed([query])[0]
        if self.reranker is not None and self.config.rerank_enabled:
            cand = self.store.search(qvec, query, scopes=scopes, tags=tags,
                                     k=self.config.rerank_candidates, now=now)
            hits = self.reranker.rerank(query, cand)[:k]
        else:
            hits = self.store.search(qvec, query, scopes=scopes, tags=tags, k=k, now=now)
```

- [ ] **Step 4: Run — expect PASS** (and full suite green)
Run: `.venv/bin/pytest tests/test_search.py -v && .venv/bin/pytest -q`

- [ ] **Step 5: Commit**
```bash
git add kb-mcp/kb/store.py kb-mcp/tests/test_search.py
git commit -m "feat(kb): rerank candidates in KnowledgeBase.search"
```

## Task 9: `Fact.slug`/`aliases` + markdown round-trip

**Files:**
- Modify: `kb-mcp/kb/models.py`, `kb-mcp/kb/markdown.py`
- Test: `kb-mcp/tests/test_markdown.py`

**Interfaces:**
- Produces: `Fact.slug`, `Fact.aliases`; both round-trip through frontmatter; plain pages get `slug = file stem`.

- [ ] **Step 1: Write the failing test**
Append to `tests/test_markdown.py`:
```python
def test_slug_aliases_round_trip(tmp_path):
    f = make_fact()
    f.slug = "ai-trends"
    f.aliases = ["AI Trends", "ml-trends"]
    back = markdown_to_fact(fact_to_markdown(f), path="x.md")
    assert back.slug == "ai-trends"
    assert back.aliases == ["AI Trends", "ml-trends"]

def test_plain_page_slug_is_stem(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "ai-trends.md").write_text("# AI trends\nbody")
    facts = read_all_facts(tmp_path)
    page = [f for f in facts if f.source and f.source.endswith("ai-trends.md")][0]
    assert page.slug == "ai-trends"
```

- [ ] **Step 2: Run — expect FAIL**
Run: `.venv/bin/pytest tests/test_markdown.py -k "slug or alias" -v`

- [ ] **Step 3: Extend `Fact`** in `kb/models.py` (after `expires_at`):
```python
    slug: str | None = None
    aliases: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Round-trip in `kb/markdown.py`**
In `fact_to_markdown`, add to the `meta` dict:
```python
        "slug": fact.slug,
        "aliases": fact.aliases,
```
In `markdown_to_fact`, add to the `Fact(...)`:
```python
        slug=meta.get("slug"),
        aliases=list(meta.get("aliases") or []),
```
In `read_all_facts`, the plain-content branch — set the slug to the file stem:
```python
                facts.append(Fact(id=str(p), scope="global", content=text.strip(),
                                  tags=[], source=str(p),
                                  ts=datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc),
                                  content_hash="", slug=p.stem))
```

- [ ] **Step 5: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_markdown.py -v && .venv/bin/pytest -q`

- [ ] **Step 6: Commit**
```bash
git add kb-mcp/kb/models.py kb-mcp/kb/markdown.py kb-mcp/tests/test_markdown.py
git commit -m "feat(kb): Fact slug/aliases + plain-page stem slug"
```

## Task 10: Wikilink parsing + link index

**Files:**
- Create: `kb-mcp/kb/links.py`
- Test: `kb-mcp/tests/test_links.py`

**Interfaces:**
- Consumes: `Fact`.
- Produces: `parse_wikilinks`, `fact_slug`, `build_link_index` (Shared Interfaces).

- [ ] **Step 1: Write the failing test**
`tests/test_links.py`:
```python
from datetime import datetime, timezone
from kb.models import Fact
from kb.links import parse_wikilinks, fact_slug, build_link_index

def f(fid, content, slug=None, aliases=(), path=None):
    return Fact(id=fid, scope="global", content=content, slug=slug,
                aliases=list(aliases), path=path,
                ts=datetime(2026, 1, 1, tzinfo=timezone.utc))

def test_parse_wikilinks():
    assert parse_wikilinks("see [[ai-trends]] and [[plans|My Plans]]") == ["ai-trends", "plans"]
    assert parse_wikilinks("none here") == []

def test_fact_slug_precedence():
    assert fact_slug(f("1", "x", slug="explicit")) == "explicit"
    assert fact_slug(f("2", "x", path="/kb/wiki/ai-trends.md")) == "ai-trends"
    assert fact_slug(f("3", "x")) == "3"

def test_build_link_index_backlinks_and_orphans():
    page = f("p", "topic page", slug="ai-trends")
    a = f("a", "see [[ai-trends]]")
    b = f("b", "links via alias [[AI Trends]]", )
    target = f("t", "the target", slug="ai-trends2", aliases=["AI Trends"])
    idx = build_link_index([page, a, b, target])
    assert "a" in idx["backlinks"]["ai-trends"]
    assert "b" in idx["backlinks"]["AI Trends"]
    # page has no inbound links -> orphan; a and b have no inbound -> orphan; target linked by alias -> not orphan
    assert "p" in idx["orphans"]
    assert "t" not in idx["orphans"]
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: kb.links`)
Run: `.venv/bin/pytest tests/test_links.py -v`

- [ ] **Step 3: Write `kb/links.py`**
```python
import re
from pathlib import Path

from kb.models import Fact

_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def parse_wikilinks(text: str) -> list[str]:
    out: list[str] = []
    for raw in _WIKILINK.findall(text or ""):
        target = raw.split("|", 1)[0].strip()
        if target:
            out.append(target)
    return out


def fact_slug(fact: Fact) -> str:
    if fact.slug:
        return fact.slug
    if fact.path:
        return Path(fact.path).stem
    return fact.id


def build_link_index(facts: list[Fact]) -> dict:
    by_slug: dict[str, Fact] = {}
    for f in facts:
        by_slug.setdefault(fact_slug(f), f)
        for a in (f.aliases or []):
            by_slug.setdefault(a, f)

    forward: dict[str, list[str]] = {}
    backlinks: dict[str, list[str]] = {}
    for f in facts:
        targets = parse_wikilinks(f.content)
        forward[f.id] = targets
        for t in targets:
            backlinks.setdefault(t, []).append(f.id)

    def has_inbound(f: Fact) -> bool:
        if backlinks.get(fact_slug(f)):
            return True
        return any(backlinks.get(a) for a in (f.aliases or []))

    orphans = [f.id for f in facts if not has_inbound(f)]
    return {"by_slug": by_slug, "forward": forward,
            "backlinks": backlinks, "orphans": orphans}
```

- [ ] **Step 4: Run — expect PASS**
Run: `.venv/bin/pytest tests/test_links.py -v`

- [ ] **Step 5: Commit**
```bash
git add kb-mcp/kb/links.py kb-mcp/tests/test_links.py
git commit -m "feat(kb): wikilink parsing + backlink/orphan index"
```

## Task 11: `get_links`/`get_backlinks`/`orphans` on KnowledgeBase + MCP tools

**Files:**
- Modify: `kb-mcp/kb/store.py`, `kb-mcp/kb/server.py`
- Test: `kb-mcp/tests/test_links_kb.py`

**Interfaces:**
- Consumes: `read_all_facts`, `build_link_index`.
- Produces: `KnowledgeBase.get_links(slug)`, `.get_backlinks(slug)`, `.orphans()` returning lists of result dicts; MCP tools `get_links`, `get_backlinks`.

- [ ] **Step 1: Write the failing test**
`tests/test_links_kb.py`:
```python
from datetime import datetime, timezone
from kb.config import Config
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 20, tzinfo=timezone.utc)

def test_backlinks_from_written_facts(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)
    # a wiki page (file stem = slug) + a fact linking to it
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "ai-trends.md").write_text("# AI trends\nbody")
    kb.write("global", "reading about [[ai-trends]] today")
    back = kb.get_backlinks("ai-trends")
    assert any("reading about" in r["content"] for r in back)

def test_orphans_lists_unlinked(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)
    kb.write("global", "an unlinked standalone fact")
    assert any("unlinked standalone" in r["content"] for r in kb.orphans())
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: get_backlinks`)
Run: `.venv/bin/pytest tests/test_links_kb.py -v`

- [ ] **Step 3: Add methods to `KnowledgeBase`** in `kb/store.py`
Add imports at top:
```python
from kb.markdown import (write_fact, append_log, write_pending_marker,
                         set_superseded, read_all_facts)
from kb.links import build_link_index
```
(Replace the existing `from kb.markdown import ...` line accordingly.) Add a result helper + methods:
```python
    def _result(self, fact: Fact) -> dict:
        return {
            "content": fact.content, "scope": fact.scope, "tags": fact.tags,
            "source": fact.source, "ts": fact.ts.isoformat() if fact.ts else None,
            "path": fact.path, "slug": fact.slug,
        }

    def _facts(self) -> list[Fact]:
        return read_all_facts(self.repo_path, include_sources=self.config.index_sources)

    def get_backlinks(self, slug: str) -> list[dict]:
        facts = self._facts()
        idx = build_link_index(facts)
        byid = {f.id: f for f in facts}
        return [self._result(byid[i]) for i in idx["backlinks"].get(slug, []) if i in byid]

    def get_links(self, slug: str) -> list[dict]:
        facts = self._facts()
        idx = build_link_index(facts)
        src = idx["by_slug"].get(slug)
        if src is None:
            return []
        out = []
        for dst in idx["forward"].get(src.id, []):
            target = idx["by_slug"].get(dst)
            out.append({"slug": dst, "resolved": self._result(target) if target else None})
        return out

    def orphans(self) -> list[dict]:
        facts = self._facts()
        idx = build_link_index(facts)
        byid = {f.id: f for f in facts}
        return [self._result(byid[i]) for i in idx["orphans"] if i in byid]
```

- [ ] **Step 4: Register MCP tools** in `kb/server.py` (after `memory_search`):
```python
    @mcp.tool()
    def get_backlinks(slug: str) -> list[dict]:
        """List facts/pages that link to the given slug via [[wikilinks]]."""
        return kb.get_backlinks(slug)

    @mcp.tool()
    def get_links(slug: str) -> list[dict]:
        """List outgoing [[wikilinks]] from the page/fact with the given slug."""
        return kb.get_links(slug)
```

- [ ] **Step 5: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_links_kb.py -v && .venv/bin/pytest -q`

- [ ] **Step 6: Commit**
```bash
git add kb-mcp/kb/store.py kb-mcp/kb/server.py kb-mcp/tests/test_links_kb.py
git commit -m "feat(kb): get_links/get_backlinks/orphans + MCP tools"
```

## Task 12: Synthesis core (`synth.py`) with a fake LLM

**Files:**
- Create: `kb-mcp/kb/synth.py`
- Test: `kb-mcp/tests/test_synth.py`

**Interfaces:**
- Consumes: `KnowledgeBase.search`, `Config.synth_*`.
- Produces: `LLMClient`, `build_messages`, `parse_citations`, `synthesize` (Shared Interfaces).

- [ ] **Step 1: Write the failing test**
`tests/test_synth.py`:
```python
from datetime import datetime, timezone
from kb.config import Config
from kb.store import KnowledgeBase
from kb.synth import build_messages, parse_citations, synthesize
from kb.models import Fact
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 20, tzinfo=timezone.utc)

class FakeLLM:
    def __init__(self, reply="From the notes, X is true [1].", record=None):
        self.reply, self.calls = reply, (record if record is not None else [])
    def complete(self, messages, model):
        self.calls.append((messages, model))
        return self.reply

def build_kb(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)

def test_build_messages_numbers_and_tags_sources():
    facts = [Fact(id="1", scope="global", content="alpha", path="/kb/memory/global/1.md")]
    msgs = build_messages("what is alpha?", facts)
    assert msgs[0]["role"] == "system"
    assert "[1]" in msgs[1]["content"] and "alpha" in msgs[1]["content"]

def test_parse_citations_maps_markers():
    facts = [Fact(id="1", scope="global", content="a", path="p1"),
             Fact(id="2", scope="project:x", content="b", path="p2")]
    cites = parse_citations("yes [2] and also [1].", facts)
    assert {c["n"] for c in cites} == {1, 2}
    assert {c["path"] for c in cites} == {"p1", "p2"}

def test_synthesize_happy_path(tmp_path):
    kb = build_kb(tmp_path)
    kb.write("global", "alpha beta gamma fact")
    llm = FakeLLM(reply="alpha beta per the note [1].")
    out = synthesize(kb, "tell me about alpha beta", llm)
    assert out["answer"] == "alpha beta per the note [1]."
    assert out["citations"] and out["citations"][0]["n"] == 1
    assert len(llm.calls) == 1

def test_synthesize_no_facts_skips_llm(tmp_path):
    kb = build_kb(tmp_path)
    llm = FakeLLM()
    out = synthesize(kb, "nothing stored about this", llm)
    assert "insufficient evidence" in out["answer"]
    assert out["citations"] == [] and llm.calls == []

def test_synthesize_llm_error_returns_error(tmp_path):
    kb = build_kb(tmp_path)
    kb.write("global", "alpha beta gamma fact")
    class Boom:
        def complete(self, messages, model): raise RuntimeError("proxy down")
    out = synthesize(kb, "alpha beta", Boom())
    assert "error" in out
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: kb.synth`)
Run: `.venv/bin/pytest tests/test_synth.py -v`

- [ ] **Step 3: Write `kb/synth.py`**
```python
import re
from typing import Protocol

from kb.models import Fact

_SYSTEM = (
    "You answer strictly from the numbered context below. Cite the sources you "
    "use as [n] inline, matching the numbers in the context. If the context does "
    "not contain the answer, reply exactly 'insufficient evidence'. Be concise."
)


class LLMClient(Protocol):
    def complete(self, messages: list[dict], model: str) -> str: ...


def build_messages(question: str, facts: list[Fact]) -> list[dict]:
    lines = []
    for i, f in enumerate(facts, 1):
        src = f.path or f.source or f.scope
        lines.append(f"[{i}] ({src}) {f.content}")
    user = f"Question: {question}\n\nContext:\n" + "\n".join(lines)
    return [{"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user}]


def parse_citations(answer: str, facts: list[Fact]) -> list[dict]:
    nums = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer or "")})
    cites = []
    for n in nums:
        if 1 <= n <= len(facts):
            f = facts[n - 1]
            cites.append({"n": n, "path": f.path, "scope": f.scope})
    return cites


def synthesize(kb, question: str, llm: LLMClient, scope=None, k: int | None = None) -> dict:
    k = k or kb.config.synth_max_facts
    results = kb.search(question, scope=scope, k=k)
    if not results:
        return {"answer": "insufficient evidence in the knowledge base",
                "citations": [], "used_facts": []}
    facts = [Fact(id="", scope=r["scope"], content=r["content"],
                  source=r["source"], path=r["path"]) for r in results]
    try:
        answer = llm.complete(build_messages(question, facts), kb.config.synth_model)
    except Exception as e:  # proxy down / timeout
        return {"error": f"synthesis failed: {e}"}
    return {"answer": answer, "citations": parse_citations(answer, facts),
            "used_facts": results}
```

- [ ] **Step 4: Run — expect PASS**
Run: `.venv/bin/pytest tests/test_synth.py -v`

- [ ] **Step 5: Commit**
```bash
git add kb-mcp/kb/synth.py kb-mcp/tests/test_synth.py
git commit -m "feat(kb): synthesis core (build_messages/parse_citations/synthesize)"
```

## Task 13: `ask` MCP tool + OpenAI-wire LLM client

**Files:**
- Modify: `kb-mcp/kb/synth.py` (add `OpenAIWireClient`), `kb-mcp/kb/server.py`
- Test: `kb-mcp/tests/test_synth.py` (client construction only; HTTP not unit-tested)

**Interfaces:**
- Consumes: `Config.synth_base_url/synth_key/synth_model`.
- Produces: `OpenAIWireClient(base_url, key)` implementing `LLMClient`; MCP tool `ask`.

- [ ] **Step 1: Write the failing test**
Append to `tests/test_synth.py`:
```python
def test_openai_wire_client_builds_url():
    from kb.synth import OpenAIWireClient
    c = OpenAIWireClient("http://claude-proxy:8000/v1", "")
    assert c.url == "http://claude-proxy:8000/v1/chat/completions"
```

- [ ] **Step 2: Run — expect FAIL** (`ImportError: OpenAIWireClient`)
Run: `.venv/bin/pytest tests/test_synth.py -k openai_wire -v`

- [ ] **Step 3: Add `OpenAIWireClient` to `kb/synth.py`**
```python
class OpenAIWireClient:
    """Minimal OpenAI-wire chat client (points at claude-proxy by default)."""

    def __init__(self, base_url: str, key: str = "", timeout: float = 60.0) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = key
        self.timeout = timeout

    def complete(self, messages: list[dict], model: str) -> str:
        import httpx
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        resp = httpx.post(self.url, json={"model": model, "messages": messages,
                                          "stream": False},
                          headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
```
Ensure `httpx` is a dependency (it is, via mcp/starlette; if `pytest` reports it missing, add `"httpx"` to `pyproject.toml` `[project] dependencies` and `pip install httpx`).

- [ ] **Step 4: Register the `ask` MCP tool** in `kb/server.py` (after `get_links`):
```python
    @mcp.tool()
    def ask(question: str, scope=None, k: int = config.synth_max_facts) -> dict:
        """Answer a question from the KB with cited sources. Returns {answer, citations, used_facts}."""
        from kb.synth import synthesize, OpenAIWireClient
        llm = OpenAIWireClient(config.synth_base_url, config.synth_key)
        return synthesize(kb, question, llm, scope=scope, k=k)
```

- [ ] **Step 5: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_synth.py -v && .venv/bin/pytest -q`

- [ ] **Step 6: Commit**
```bash
git add kb-mcp/kb/synth.py kb-mcp/kb/server.py kb-mcp/pyproject.toml
git commit -m "feat(kb): ask MCP tool + OpenAI-wire synthesis client"
```

## Task 14: Consolidation (`consolidate.py`) + `kb consolidate` CLI

**Files:**
- Create: `kb-mcp/kb/consolidate.py`
- Modify: `kb-mcp/kb/cli.py`
- Test: `kb-mcp/tests/test_consolidate.py`

**Interfaces:**
- Consumes: `read_all_facts`, `set_superseded`, `build_link_index`, `lint._normalize`, `Config.dedup_merge/automerge/stale_days`, `VectorStore.mark_superseded`.
- Produces: `consolidate(store, embedder, repo_path, config, apply=False, now=None) -> report`; `kb consolidate [--apply]`.

- [ ] **Step 1: Write the failing test**
`tests/test_consolidate.py`:
```python
from datetime import datetime, timezone, timedelta
from kb.config import Config
from kb.store import KnowledgeBase
from kb.consolidate import consolidate
from kb.reindex import reindex
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 20, tzinfo=timezone.utc)

def build(tmp_path, store):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(store, emb, tmp_path, cfg, clock=lambda: FIXED), cfg, emb

def test_reports_without_apply(tmp_path):
    store = InMemoryVectorStore(FakeEmbedder())
    kb, cfg, emb = build(tmp_path, store)
    kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota")
    kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota kappa")  # near-dup
    report = consolidate(store, emb, tmp_path, cfg, apply=False, now=FIXED)
    assert report["near_dups"]            # detected
    assert report["auto_merged"] == []    # nothing changed without apply

def test_auto_merge_supersedes_and_survives_reindex(tmp_path):
    store = InMemoryVectorStore(FakeEmbedder())
    kb, cfg, emb = build(tmp_path, store)
    a = kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota")
    b = kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota kappa")
    report = consolidate(store, emb, tmp_path, cfg, apply=True, now=FIXED)
    assert report["auto_merged"]
    # rebuild index from markdown; the superseded fact stays hidden
    fresh = InMemoryVectorStore(emb)
    reindex(fresh, emb, tmp_path, cfg)
    kb2 = KnowledgeBase(fresh, emb, tmp_path, cfg, clock=lambda: FIXED)
    paths = {r["path"] for r in kb2.search("alpha beta", scope=["global"], k=10)}
    assert not (a["path"] in paths and b["path"] in paths)  # not both active

def test_staleness_report_only(tmp_path):
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x", stale_days=10)
    emb = FakeEmbedder()
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    kb = KnowledgeBase(store, emb, tmp_path, cfg, clock=lambda: old)
    kb.write("global", "an old standalone fact")
    report = consolidate(store, emb, tmp_path, cfg, apply=True, now=FIXED)
    assert any("old standalone" in s["content"] for s in report["stale"])
    # report-only: the stale fact is NOT superseded
    assert report["auto_merged"] == []
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: kb.consolidate`)
Run: `.venv/bin/pytest tests/test_consolidate.py -v`

- [ ] **Step 3: Write `kb/consolidate.py`**
```python
from datetime import datetime, timezone, timedelta

from kb.links import build_link_index
from kb.lint import _normalize
from kb.markdown import read_all_facts, set_superseded, append_log


def _cos(u, v):
    return sum(x * y for x, y in zip(u, v))


def consolidate(store, embedder, repo_path, config, apply: bool = False,
                now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    facts = read_all_facts(repo_path, include_sources=False)
    report = {"near_dups": [], "auto_merged": [], "stale": [],
              "orphans": [], "tag_drift": []}
    if not facts:
        return report

    vecs = embedder.embed([f.content for f in facts])
    byid = {f.id: f for f in facts}

    # near-duplicates (same scope), and auto-merge the strict subset
    for i in range(len(facts)):
        for j in range(i + 1, len(facts)):
            if facts[i].scope != facts[j].scope:
                continue
            sim = _cos(vecs[i], vecs[j])
            if sim < config.dedup_merge:
                continue
            a, b = facts[i], facts[j]
            report["near_dups"].append({"a": a.id, "b": b.id, "sim": round(sim, 4)})
            if apply and sim >= config.automerge:
                # keep the newer fact; supersede the older (non-destructive)
                older, newer = sorted([a, b], key=lambda f: f.ts or now)
                if older.superseded_by:
                    continue
                set_superseded(repo_path, older, newer.id)
                store.mark_superseded(older.id, newer.id)
                report["auto_merged"].append({"superseded": older.id, "into": newer.id,
                                              "sim": round(sim, 4)})

    # staleness (report-only)
    cutoff = now - timedelta(days=config.stale_days)
    for f in facts:
        if f.ts and f.ts < cutoff:
            report["stale"].append({"id": f.id, "content": f.content[:80], "ts": f.ts.isoformat()})

    # orphans (report-only)
    idx = build_link_index(facts)
    report["orphans"] = [{"id": i, "content": byid[i].content[:80]}
                         for i in idx["orphans"] if i in byid]

    # tag drift (report-only) — same normalize rule as kb lint
    tags = sorted({t for f in facts for t in f.tags})
    for x in range(len(tags)):
        for y in range(x + 1, len(tags)):
            if tags[x] != tags[y] and _normalize(tags[x]) == _normalize(tags[y]):
                report["tag_drift"].append((tags[x], tags[y]))

    _write_report(repo_path, now, report, apply)
    return report


def _write_report(repo_path, now, report, apply) -> None:
    d = repo_path / ".kb" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{now.date().isoformat()}.md"
    lines = [f"# Consolidation {now.isoformat()} (apply={apply})", ""]
    for key in ("auto_merged", "near_dups", "stale", "orphans", "tag_drift"):
        lines.append(f"## {key} ({len(report[key])})")
        for item in report[key]:
            lines.append(f"- {item}")
        lines.append("")
    p.write_text("\n".join(lines))
    append_log(repo_path,
               f"## [{now.date().isoformat()}] consolidate | apply={apply} | "
               f"merged={len(report['auto_merged'])} dups={len(report['near_dups'])} "
               f"stale={len(report['stale'])} orphans={len(report['orphans'])}")
```

- [ ] **Step 4: Run — expect PASS**
Run: `.venv/bin/pytest tests/test_consolidate.py -v`

- [ ] **Step 5: Add the `consolidate` CLI subcommand** in `kb/cli.py`
Add the parser (after the `lint` parser):
```python
    cons = sub.add_parser("consolidate", help="report KB health; auto-merge near-dups with --apply")
    cons.add_argument("--apply", action="store_true", help="apply safe auto-merges")
```
Add the branch (after the `lint` branch, before `return 1`):
```python
    if args.cmd == "consolidate":
        from kb.consolidate import consolidate
        report = consolidate(store, embedder, cfg.repo_path, cfg, apply=args.apply)
        print(f"auto_merged={len(report['auto_merged'])} near_dups={len(report['near_dups'])} "
              f"stale={len(report['stale'])} orphans={len(report['orphans'])} "
              f"tag_drift={len(report['tag_drift'])}")
        report_only = len(report["near_dups"]) - len(report["auto_merged"]) \
            + len(report["stale"]) + len(report["orphans"]) + len(report["tag_drift"])
        return 1 if report_only else 0
```

- [ ] **Step 6: Add a CLI consolidate test**
Append to `tests/test_cli.py`:
```python
def test_consolidate_subcommand(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from kb.store import KnowledgeBase
    from tests.fakes import FakeEmbedder, InMemoryVectorStore
    import datetime as _dt
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    store = InMemoryVectorStore(emb)
    kb = KnowledgeBase(store, emb, tmp_path, cfg,
                       clock=lambda: _dt.datetime(2026, 6, 20, tzinfo=_dt.timezone.utc))
    kb.write("global", "a standalone orphan fact")
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    rc = cli.main(["consolidate"])
    assert "orphans=" in capsys.readouterr().out
    assert rc == 1  # orphan reported
```

- [ ] **Step 7: Run — expect PASS** (and full suite)
Run: `.venv/bin/pytest tests/test_consolidate.py tests/test_cli.py -v && .venv/bin/pytest -q`

- [ ] **Step 8: Commit**
```bash
git add kb-mcp/kb/consolidate.py kb-mcp/kb/cli.py kb-mcp/tests/test_consolidate.py kb-mcp/tests/test_cli.py
git commit -m "feat(kb): nightly consolidation (report + safe auto-merge) + CLI"
```

## Task 15: Wiring — compose env, reranker preload, schedule, build_kb

**Files:**
- Modify: `~/development/hermes-test/docker-compose.yml`, `~/development/hermes-test/.env.example`
- Modify: `kb-mcp/kb/server.py` (`build_kb` wires the reranker)
- Modify: `kb-mcp/Dockerfile` (optional reranker model note)
- Create: `docs/reference/kb-consolidate.plist.snapshot`, `docs/reference/spec-a-ops.md`

**Interfaces:**
- Produces: a deployed stack where search reranks, `ask` works, and consolidation runs nightly.

- [ ] **Step 1: Wire the reranker into `build_kb`** in `kb/server.py`
Replace `build_kb`:
```python
def build_kb(config: Config) -> KnowledgeBase:
    store = PgVectorStore(connect(config.db_url), dim=config.embed_dim)
    store.ensure_schema()
    embedder = FastEmbedder(model=config.embed_model, dim=config.embed_dim)
    reranker = None
    if config.rerank_enabled:
        from kb.rerank import FastReranker
        reranker = FastReranker(model=config.rerank_model)
    return KnowledgeBase(store, embedder, config.repo_path, config, reranker=reranker)
```

- [ ] **Step 2: Add the new env to compose** — in `~/development/hermes-test/docker-compose.yml`, under the `kb-mcp` `environment:` list add:
```yaml
      - KB_RERANK_ENABLED=${KB_RERANK_ENABLED:-true}
      - KB_RERANK_MODEL=${KB_RERANK_MODEL:-BAAI/bge-reranker-base}
      - KB_RERANK_CANDIDATES=${KB_RERANK_CANDIDATES:-30}
      - KB_SYNTH_BASE_URL=${KB_SYNTH_BASE_URL:-http://claude-proxy:8000/v1}
      - KB_SYNTH_MODEL=${KB_SYNTH_MODEL:-claude-sonnet-4-6}
      - KB_SYNTH_KEY=${KB_SYNTH_KEY:-${HERMES_PROXY_KEY:-}}
      - KB_SYNTH_MAX_FACTS=${KB_SYNTH_MAX_FACTS:-8}
      - KB_STALE_DAYS=${KB_STALE_DAYS:-180}
      - KB_AUTOMERGE=${KB_AUTOMERGE:-0.97}
```
And document them in `.env.example` (names only, sane defaults shown in comments).

- [ ] **Step 3: Pre-pull the reranker model in the image (optional, avoids first-call latency)** — in `kb-mcp/Dockerfile`, after the embed-model ENV line add:
```dockerfile
ENV KB_RERANK_MODEL=BAAI/bge-reranker-base
```

- [ ] **Step 4: Create the nightly schedule snapshot** `docs/reference/kb-consolidate.plist.snapshot`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>dev.kb.consolidate</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd $HOME/development/hermes-test && docker compose --env-file .env exec -T kb-mcp kb consolidate --apply</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>/tmp/kb-consolidate.log</string>
  <key>StandardErrorPath</key><string>/tmp/kb-consolidate.err</string>
</dict>
</plist>
```

- [ ] **Step 5: Write `docs/reference/spec-a-ops.md`** documenting: how `ask`/`get_backlinks` appear as MCP tools; how to install the consolidate schedule (`cp` the plist to `~/Library/LaunchAgents/` then `launchctl load`); the env knobs; and that the reranker/synth are configurable.

- [ ] **Step 6: Rebuild + live verify**
```bash
cd ~/development/hermes-test
docker compose --env-file .env up -d --build kb-mcp
for i in $(seq 1 60); do curl -fsS http://127.0.0.1:8077/health >/dev/null 2>&1 && { echo healthy; break; }; sleep 3; done
# search still works (now reranked):
docker compose --env-file .env exec -T kb-mcp kb reindex
# consolidate dry run:
docker compose --env-file .env exec -T kb-mcp kb consolidate
```
Expected: `healthy`, `indexed N facts`, and a consolidation summary line.

- [ ] **Step 7: Commit**
```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/server.py kb-mcp/Dockerfile docs/reference/kb-consolidate.plist.snapshot docs/reference/spec-a-ops.md
git commit -m "feat(kb): wire reranker into build_kb; compose env + consolidate schedule + docs"
cd ~/development/hermes-test && git add docker-compose.yml .env.example 2>/dev/null || true
```
(Note: `hermes-test` may not be a git repo; if `git add` errors there, skip — those edits are local config.)

---

## Self-Review

**1. Spec coverage:**
- §2/§4 synthesis via claude-proxy, cited → Tasks 12, 13 (+config 6). ✓
- §2/§5 local reranking → Tasks 7, 8 (+config 6, wiring 15). ✓
- §6 wikilinks (parse, backlink index, get_links/get_backlinks, orphans, slug/aliases) → Tasks 9, 10, 11. ✓
- §6 no ranking boost → confirmed (links are navigational/orphan only). ✓
- §6b Obsidian (slugs, aliases, .obsidian ignore, vault = repo) → Tasks 9 (slug), 2/3 (gitignore .obsidian, vault mount). ✓
- §7 consolidation (local checks, safe near-dup auto-merge, supersede-not-delete, report, schedule) → Tasks 14, 15. ✓
- §8 error handling (synth down, reranker fallback, dangling links) → Task 12 (error path), 13; reranker fallback = `reranker is None`/disabled path Task 8; dangling links retained Task 10. ✓
- §9 TDD targets (rerank, wikilinks, synthesis w/ fake LLM + no-facts + error, consolidation apply vs report) → Tasks 7,10,12,14. ✓
- §11 deployment/privacy (vault, agent-kb, KB_HOST_PATH, example, @import repoint, drop mirror, history scrub) → Phase 0 Tasks 1–5. ✓
- §10 build order (rerank → wikilinks → synthesis → consolidation, Phase 0 first) → matches task order. ✓

No gaps.

**2. Placeholder scan:** No TBD/“handle edge cases”/“similar to Task N”. Every code step shows complete code; Phase 0 steps show exact commands. The only intentional non-literals are `${KB_HOST_PATH}` / `<the user's Obsidian vault>` — required by the privacy constraint, set in gitignored `.env`. ✓

**3. Type consistency:** `Config` fields, `Fact.slug/aliases`, `Reranker.rerank`, `KnowledgeBase(..., reranker=)` + `.search`/`.get_backlinks`/`.get_links`/`.orphans`/`._result`/`._facts`, `parse_wikilinks`/`fact_slug`/`build_link_index` keys (`by_slug`/`forward`/`backlinks`/`orphans`), `LLMClient.complete(messages, model)`, `synthesize(kb, question, llm, scope, k)`, `consolidate(store, embedder, repo_path, config, apply, now)` — all consistent across tasks and matched to the real existing signatures (`store.search(qvec, query, scopes=, tags=, k=, now=)`, `store.mark_superseded`, `set_superseded(repo_path, old_fact, new_id)`, `read_all_facts(repo_path, include_sources=)`, `reindex(store, embedder, repo_path, config)`). ✓
