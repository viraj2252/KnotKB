# Shared Knowledge Base Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one personal knowledge base — markdown as source of truth — that both Claude Code and the Hermes agent read and write through a single shared HTTP MCP server backed by a local pgvector index.

**Architecture:** A Python package `kb` exposes a pure engine (`KnowledgeBase`) behind two protocols (`Embedder`, `VectorStore`) so the write-side logic is unit-testable against in-memory fakes. `kb/server.py` is a thin FastMCP HTTP adapter (bearer-auth) exposing `memory_write` / `memory_search`. Markdown files under the KB repo are the source of truth; pgvector is a derived, rebuildable index. Durability is a private git remote + a one-way Google Drive mirror; the DB is rebuild-only via `kb reindex`.

**Tech Stack:** Python 3.12, `mcp` (FastMCP, streamable-HTTP), `fastembed` (ONNX CPU, `BAAI/bge-small-en-v1.5`, 384-dim), `psycopg[binary]` + `pgvector`, `pyyaml`, `uvicorn`, `pytest`; Docker Compose (`pgvector/pgvector:pg16`); `rclone` for the Drive mirror.

## Global Constraints

- **Source of truth is markdown+git.** The DB is never the store of record and is never synced/backed up. [spec §2, §7]
- **One shared HTTP MCP server**, single long-lived process; writes are serialized by it. [spec §2, §3]
- **Embeddings are local, in-process**, model `BAAI/bge-small-en-v1.5`, dimension **384**. No API calls. [spec §2]
- **Scopes** are exactly: `global`, `project:<name>`, `agent:<name>:scratch`. Malformed scope → reject. [spec §5, §8]
- **Durable scopes** (`global`, `project:*`) → write markdown **then** index. **`agent:*:scratch`** → pgvector-only, ephemeral (TTL), never written to git. [spec §5, §6]
- **Markdown is written before indexing**; on DB failure, write a pending-reindex marker and still succeed. [spec §6, §8]
- **Dedup thresholds:** merge ≥ `0.92`, skip ≥ `0.98` cosine similarity (configurable). [spec §6, §12]
- **Search default scope** when omitted = `["global"]`; `scope` may be a string or list; optional `tags` topic filter; exclude superseded and expired rows. [spec §5, §6]
- **Scratch TTL** default 86400s. [spec §12]
- **`sources/` is not indexed** in v1. Reindex covers `memory/`, `wiki/`, `decisions/`. [spec §12]
- All ports bind `127.0.0.1`. Bearer token `KB_MCP_KEY` required by the server. [spec §7]
- TDD throughout: failing test → minimal code → pass → commit. DRY, YAGNI.

## Shared Interfaces (canonical signatures — every task must match these exactly)

```python
# kb/config.py
@dataclass(frozen=True)
class Config:
    repo_path: Path
    db_url: str
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    dedup_merge: float = 0.92
    dedup_skip: float = 0.98
    scratch_ttl_seconds: int = 86400
    mcp_key: str = ""
    mcp_port: int = 8077
    index_sources: bool = False
    @staticmethod
    def from_env(env: Mapping[str, str]) -> "Config": ...

# kb/models.py
@dataclass
class Fact:
    id: str
    scope: str
    content: str
    tags: list[str]
    source: str | None
    ts: datetime                 # timezone-aware UTC
    content_hash: str
    superseded_by: str | None = None
    path: str | None = None      # markdown path (durable) or None (scratch)
    expires_at: datetime | None = None

# kb/util.py
def content_hash(content: str) -> str            # sha256 hexdigest
def make_id(ts: datetime, chash: str) -> str     # "YYYYMMDDHHMMSS-<6hex>"
def validate_scope(scope: str) -> None           # raise ValueError if malformed
def is_scratch(scope: str) -> bool
def scope_dir(scope: str) -> str                 # "global" | "project/<name>"; raises for scratch

# kb/dedup.py
@dataclass
class DedupConfig:
    merge_threshold: float
    skip_threshold: float
def decide(best_similarity: float | None, cfg: DedupConfig) -> str   # "created"|"merged"|"skipped"

# kb/embeddings.py
class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...
class FastEmbedder:                              # wraps fastembed
    def __init__(self, model: str, dim: int): ...

# kb/db.py
def rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]
class VectorStore(Protocol):
    def upsert(self, fact: Fact, vector: list[float]) -> None: ...
    def nearest(self, vector: list[float], scope: str, k: int = 1) -> list[tuple[Fact, float]]: ...
    def search(self, query_vector: list[float], query_text: str, scopes: list[str],
               tags: list[str] | None, k: int, now: datetime) -> list[tuple[Fact, float]]: ...
    def mark_superseded(self, old_id: str, new_id: str) -> None: ...
    def delete_expired_scratch(self, now: datetime) -> int: ...
    def clear(self) -> None: ...
class PgVectorStore:                             # real implementation of VectorStore

# kb/store.py
class KnowledgeBase:
    def __init__(self, store: VectorStore, embedder: Embedder, repo_path: Path,
                 config: Config, clock: Callable[[], datetime] = ...): ...
    def write(self, scope: str, content: str, tags: list[str] | None = None,
              source: str | None = None) -> dict      # {"id","path","action"}
    def search(self, query: str, scope: str | list[str] | None = None,
               tags: list[str] | None = None, k: int = 8) -> list[dict]
```

A "result dict" from `search` has keys: `content, score, scope, tags, source, ts, path`.

---

## Task 1: KB repo scaffold + seed context

**Files:**
- Create: `~/development/knowledge-base/.gitignore`
- Create: `~/development/knowledge-base/index.md`, `log.md`, `README.md`
- Create: `context/about-me.md`, `context/about-flintt.md`, `context/priorities.md`, `context/preferences.md`
- Create: `wiki/index.md`
- Create: `memory/global/.gitkeep`, `memory/project/.gitkeep`, `decisions/.gitkeep`, `sources/.gitkeep`

**Interfaces:**
- Consumes: nothing.
- Produces: the on-disk repo layout from spec §4 that every later task reads/writes.

- [ ] **Step 1: Create the directory tree and placeholders**

```bash
cd ~/development/knowledge-base
mkdir -p context wiki memory/global memory/project decisions sources
touch memory/global/.gitkeep memory/project/.gitkeep decisions/.gitkeep sources/.gitkeep
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
# Python
__pycache__/
*.pyc
.venv/
.pytest_cache/
*.egg-info/

# Local env / secrets
.env

# Pending-reindex markers are runtime state, not source of truth
.kb/pending/
```

- [ ] **Step 3: Write top-level `index.md` and `log.md`**

`index.md`:
```markdown
# Knowledge Base — Index

Top-level catalog. Source of truth is the markdown in this repo; the pgvector
index is derived and rebuildable (`kb reindex`).

- `context/` — durable identity & business context (Claude Code @imports `about-me.md`)
- `wiki/` — curated synthesis pages, one per topic (see `wiki/index.md`)
- `decisions/` — append-only dated decisions
- `memory/` — atomic facts written by the `memory_write` tool
- `sources/` — immutable raw transcripts/drops (distilled by the kb-ingest skill)
```

`log.md`:
```markdown
# KB Log

Append-only chronological record of writes and ingests.
Format: `## [YYYY-MM-DD] write | <scope> | <summary>`
```

- [ ] **Step 4: Write seed `context/` files**

`context/about-me.md`:
```markdown
# About Me

VJ — based in Melbourne. Work spans Flintt/Liberty. Senior engineer; prefers
accuracy over agreement and explicit confidence tags ([Certain]/[Likely]/[Guessing]).

> Seed file. Expand over time; this is @import'd into ~/.claude/CLAUDE.md so keep it concise.
```

`context/about-flintt.md`:
```markdown
# About Flintt

Business/work context (stack, team, identifiers). Seed file — fill in over time.
```

`context/priorities.md`:
```markdown
# Current Priorities

Top-of-mind work and goals. Seed file — keep current.
```

`context/preferences.md`:
```markdown
# Working Preferences

- Senior advisor mode: accuracy over agreement; tag confidence.
- Python, TDD, spec-driven. Run pytest, not unittest.
- Prose over bullets unless asked.
```

- [ ] **Step 5: Write `wiki/index.md`**

```markdown
# Wiki Index

Curated synthesis pages, one per topic (emergent). Examples:
`ai-trends.md`, `startup-ideas.md`, `business-ideas.md`, `life-lessons.md`, `radar.md`.

Pages are authored by the `kb-ingest` skill from `sources/`, never by `memory_write`.
```

- [ ] **Step 6: Commit**

```bash
cd ~/development/knowledge-base
git add -A
git commit -m "feat: scaffold KB repo layout and seed context"
```

---

## Task 2: Claude Code global wiring

**Files:**
- Create: `~/.claude/CLAUDE.md`

**Interfaces:**
- Consumes: `context/about-me.md` from Task 1 (via `@import`).
- Produces: global Claude Code memory that points at the KB and imports identity.

- [ ] **Step 1: Verify the import target exists**

Run: `test -f ~/development/knowledge-base/context/about-me.md && echo OK`
Expected: `OK`

- [ ] **Step 2: Write `~/.claude/CLAUDE.md`**

```markdown
# Global preferences
- Senior advisor mode: accuracy over agreement. Tag confidence [Certain]/[Likely]/[Guessing].
- Python, TDD, spec-driven. Run pytest, not unittest.
- Prose over bullets unless asked.

# Knowledge base
Durable personal/business knowledge lives at ~/development/knowledge-base/.
A shared MCP server `kb` exposes memory_search / memory_write over it.
Prefer memory_search for recall; read context/ and wiki/index.md on demand.
Do NOT inline-read the whole KB; pull only what the task needs.

@~/development/knowledge-base/context/about-me.md
```

- [ ] **Step 3: Verify it parses in a fresh session**

Run: `claude -p "In one line, what file path holds my durable knowledge base?"`
Expected: response mentions `~/development/knowledge-base/`.

- [ ] **Step 4: Commit (note: this file is outside the repo; record the content in the repo for reference)**

```bash
cd ~/development/knowledge-base
mkdir -p docs/reference
cp ~/.claude/CLAUDE.md docs/reference/claude-md.snapshot.md
git add docs/reference/claude-md.snapshot.md
git commit -m "docs: snapshot global CLAUDE.md KB wiring"
```

---

## Task 3: Python project setup + Config

**Files:**
- Create: `kb-mcp/pyproject.toml`
- Create: `kb-mcp/kb/__init__.py`
- Create: `kb-mcp/kb/config.py`
- Test: `kb-mcp/tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config` (see Shared Interfaces) used by every server/CLI entrypoint.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "kb-mcp"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "mcp>=1.2.0",
    "fastembed>=0.4.0",
    "psycopg[binary]>=3.2",
    "pgvector>=0.3.6",
    "pyyaml>=6.0",
    "uvicorn>=0.30",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
kb = "kb.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
include = ["kb*"]
```

- [ ] **Step 2: Create the package and a virtualenv**

```bash
cd ~/development/knowledge-base/kb-mcp
mkdir -p kb tests
touch kb/__init__.py tests/__init__.py
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

- [ ] **Step 3: Write the failing test**

`tests/test_config.py`:
```python
from pathlib import Path
from kb.config import Config

def test_defaults_applied_with_minimal_env():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "postgresql://x"})
    assert cfg.repo_path == Path("/kb")
    assert cfg.db_url == "postgresql://x"
    assert cfg.embed_model == "BAAI/bge-small-en-v1.5"
    assert cfg.embed_dim == 384
    assert cfg.dedup_merge == 0.92
    assert cfg.dedup_skip == 0.98
    assert cfg.scratch_ttl_seconds == 86400
    assert cfg.mcp_port == 8077
    assert cfg.index_sources is False

def test_overrides_from_env():
    cfg = Config.from_env({
        "KB_REPO_PATH": "/kb", "KB_DB_URL": "postgresql://x",
        "KB_DEDUP_MERGE": "0.8", "KB_DEDUP_SKIP": "0.95",
        "KB_SCRATCH_TTL_SECONDS": "60", "KB_MCP_PORT": "9000",
        "KB_MCP_KEY": "secret", "KB_INDEX_SOURCES": "true",
    })
    assert cfg.dedup_merge == 0.8
    assert cfg.dedup_skip == 0.95
    assert cfg.scratch_ttl_seconds == 60
    assert cfg.mcp_port == 9000
    assert cfg.mcp_key == "secret"
    assert cfg.index_sources is True
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd ~/development/knowledge-base/kb-mcp && .venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.config'`

- [ ] **Step 5: Write `kb/config.py`**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    repo_path: Path
    db_url: str
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    dedup_merge: float = 0.92
    dedup_skip: float = 0.98
    scratch_ttl_seconds: int = 86400
    mcp_key: str = ""
    mcp_port: int = 8077
    index_sources: bool = False

    @staticmethod
    def from_env(env: Mapping[str, str]) -> "Config":
        def flag(name: str, default: bool) -> bool:
            v = env.get(name)
            return default if v is None else v.strip().lower() in ("1", "true", "yes")

        return Config(
            repo_path=Path(env["KB_REPO_PATH"]),
            db_url=env["KB_DB_URL"],
            embed_model=env.get("KB_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
            embed_dim=int(env.get("KB_EMBED_DIM", "384")),
            dedup_merge=float(env.get("KB_DEDUP_MERGE", "0.92")),
            dedup_skip=float(env.get("KB_DEDUP_SKIP", "0.98")),
            scratch_ttl_seconds=int(env.get("KB_SCRATCH_TTL_SECONDS", "86400")),
            mcp_key=env.get("KB_MCP_KEY", ""),
            mcp_port=int(env.get("KB_MCP_PORT", "8077")),
            index_sources=flag("KB_INDEX_SOURCES", False),
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/pyproject.toml kb-mcp/kb/__init__.py kb-mcp/kb/config.py kb-mcp/tests/__init__.py kb-mcp/tests/test_config.py
git commit -m "feat(kb): project setup and Config.from_env"
```

---

## Task 4: Scope/id utilities

**Files:**
- Create: `kb-mcp/kb/util.py`
- Test: `kb-mcp/tests/test_util.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `content_hash`, `make_id`, `validate_scope`, `is_scratch`, `scope_dir` (see Shared Interfaces).

- [ ] **Step 1: Write the failing test**

`tests/test_util.py`:
```python
from datetime import datetime, timezone
import pytest
from kb.util import content_hash, make_id, validate_scope, is_scratch, scope_dir

def test_content_hash_stable():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("a") != content_hash("b")

def test_make_id_format():
    ts = datetime(2026, 6, 18, 9, 30, 5, tzinfo=timezone.utc)
    cid = make_id(ts, content_hash("x"))
    assert cid.startswith("20260618093005-")
    assert len(cid.split("-")[1]) == 6

@pytest.mark.parametrize("scope", ["global", "project:hermes-test", "agent:claude:scratch"])
def test_valid_scopes(scope):
    validate_scope(scope)  # no raise

@pytest.mark.parametrize("scope", ["", "project:", "agent::scratch", "agent:x", "weird", "project:a:b"])
def test_invalid_scopes_raise(scope):
    with pytest.raises(ValueError):
        validate_scope(scope)

def test_is_scratch():
    assert is_scratch("agent:claude:scratch") is True
    assert is_scratch("global") is False

def test_scope_dir():
    assert scope_dir("global") == "global"
    assert scope_dir("project:hermes-test") == "project/hermes-test"

def test_scope_dir_rejects_scratch():
    with pytest.raises(ValueError):
        scope_dir("agent:claude:scratch")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_util.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.util'`

- [ ] **Step 3: Write `kb/util.py`**

```python
import hashlib
import re
from datetime import datetime

_PROJECT_RE = re.compile(r"^project:[A-Za-z0-9._-]+$")
_SCRATCH_RE = re.compile(r"^agent:[A-Za-z0-9._-]+:scratch$")


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def make_id(ts: datetime, chash: str) -> str:
    return ts.strftime("%Y%m%d%H%M%S") + "-" + chash[:6]


def validate_scope(scope: str) -> None:
    if scope == "global" or _PROJECT_RE.match(scope) or _SCRATCH_RE.match(scope):
        return
    raise ValueError(f"malformed scope: {scope!r}")


def is_scratch(scope: str) -> bool:
    return bool(_SCRATCH_RE.match(scope))


def scope_dir(scope: str) -> str:
    validate_scope(scope)
    if scope == "global":
        return "global"
    if scope.startswith("project:"):
        return "project/" + scope.split(":", 1)[1]
    raise ValueError(f"scope has no markdown dir: {scope!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_util.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/util.py kb-mcp/tests/test_util.py
git commit -m "feat(kb): scope and id utilities"
```

---

## Task 5: Dedup decision

**Files:**
- Create: `kb-mcp/kb/dedup.py`
- Test: `kb-mcp/tests/test_dedup.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DedupConfig`, `decide(best_similarity, cfg)` (see Shared Interfaces).

- [ ] **Step 1: Write the failing test**

`tests/test_dedup.py`:
```python
from kb.dedup import DedupConfig, decide

CFG = DedupConfig(merge_threshold=0.92, skip_threshold=0.98)

def test_no_neighbor_creates():
    assert decide(None, CFG) == "created"

def test_below_merge_creates():
    assert decide(0.50, CFG) == "created"
    assert decide(0.9199, CFG) == "created"

def test_merge_band():
    assert decide(0.92, CFG) == "merged"
    assert decide(0.9799, CFG) == "merged"

def test_skip_band():
    assert decide(0.98, CFG) == "skipped"
    assert decide(1.0, CFG) == "skipped"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dedup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.dedup'`

- [ ] **Step 3: Write `kb/dedup.py`**

```python
from dataclasses import dataclass


@dataclass
class DedupConfig:
    merge_threshold: float
    skip_threshold: float


def decide(best_similarity: float | None, cfg: DedupConfig) -> str:
    if best_similarity is None:
        return "created"
    if best_similarity >= cfg.skip_threshold:
        return "skipped"
    if best_similarity >= cfg.merge_threshold:
        return "merged"
    return "created"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_dedup.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/dedup.py kb-mcp/tests/test_dedup.py
git commit -m "feat(kb): dedup decision logic"
```

---

## Task 6: RRF fusion

**Files:**
- Create: `kb-mcp/kb/db.py` (start it with `rrf_fuse` only; the store class is added in Task 11)
- Test: `kb-mcp/tests/test_rrf.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `rrf_fuse(ranked_lists, k=60)` (see Shared Interfaces).

- [ ] **Step 1: Write the failing test**

`tests/test_rrf.py`:
```python
from kb.db import rrf_fuse

def test_consensus_ranks_first():
    vector = ["a", "b", "c"]
    fts = ["a", "c", "d"]
    fused = rrf_fuse([vector, fts])
    ids = [i for i, _ in fused]
    assert ids[0] == "a"            # top of both lists
    assert set(ids) == {"a", "b", "c", "d"}

def test_deterministic_and_tie_broken_by_id():
    fused1 = rrf_fuse([["x", "y"], ["y", "x"]])
    fused2 = rrf_fuse([["x", "y"], ["y", "x"]])
    assert fused1 == fused2
    # x and y have identical fused scores -> stable order by id
    assert [i for i, _ in fused1] == ["x", "y"]

def test_empty_lists():
    assert rrf_fuse([[], []]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_rrf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.db'`

- [ ] **Step 3: Write `kb/db.py` (rrf only for now)**

```python
def rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal-rank fusion. Deterministic; ties broken by id ascending."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_rrf.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/db.py kb-mcp/tests/test_rrf.py
git commit -m "feat(kb): reciprocal-rank fusion"
```

---

## Task 7: Fact model + markdown serialization

**Files:**
- Create: `kb-mcp/kb/models.py`
- Create: `kb-mcp/kb/markdown.py`
- Test: `kb-mcp/tests/test_markdown.py`

**Interfaces:**
- Consumes: `Fact` (models.py); `content_hash`, `make_id`, `scope_dir` (util.py).
- Produces:
  - `Fact` dataclass (Shared Interfaces).
  - `fact_to_markdown(fact) -> str`, `markdown_to_fact(text, path) -> Fact`
  - `write_fact(repo_path, fact) -> Path` (sets `fact.path`, returns path)
  - `read_all_facts(repo_path, include_sources=False) -> list[Fact]`
  - `append_log(repo_path, line) -> None`
  - `write_pending_marker(repo_path, fact_id) -> None`, `read_pending_markers(repo_path) -> list[str]`, `clear_pending_marker(repo_path, fact_id) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_markdown.py`:
```python
from datetime import datetime, timezone
from pathlib import Path
from kb.models import Fact
from kb.markdown import (
    fact_to_markdown, markdown_to_fact, write_fact, read_all_facts,
    append_log, write_pending_marker, read_pending_markers, clear_pending_marker,
)

def make_fact(content="alpha beta", scope="global", tags=("ai-trends",)):
    ts = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)
    from kb.util import content_hash, make_id
    ch = content_hash(content)
    return Fact(id=make_id(ts, ch), scope=scope, content=content,
                tags=list(tags), source="conv", ts=ts, content_hash=ch)

def test_round_trip(tmp_path):
    f = make_fact()
    text = fact_to_markdown(f)
    back = markdown_to_fact(text, path="x.md")
    assert back.id == f.id
    assert back.scope == f.scope
    assert back.content == f.content
    assert back.tags == f.tags
    assert back.source == f.source
    assert back.ts == f.ts
    assert back.content_hash == f.content_hash

def test_write_fact_places_file_by_scope(tmp_path):
    f = make_fact(scope="project:hermes-test")
    p = write_fact(tmp_path, f)
    assert p.exists()
    assert p.parent == tmp_path / "memory" / "project" / "hermes-test"
    assert f.path == str(p)

def test_read_all_facts_collects_memory(tmp_path):
    write_fact(tmp_path, make_fact(content="alpha"))
    write_fact(tmp_path, make_fact(content="beta gamma", scope="project:p"))
    facts = read_all_facts(tmp_path)
    contents = sorted(f.content for f in facts)
    assert contents == ["alpha", "beta gamma"]

def test_read_all_facts_excludes_sources_by_default(tmp_path):
    (tmp_path / "sources").mkdir(parents=True)
    (tmp_path / "sources" / "raw.md").write_text("# raw transcript\nlots of noise")
    write_fact(tmp_path, make_fact(content="alpha"))
    facts = read_all_facts(tmp_path)
    assert all("transcript" not in f.content for f in facts)

def test_append_log(tmp_path):
    append_log(tmp_path, "## [2026-06-18] write | global | alpha")
    assert "alpha" in (tmp_path / "log.md").read_text()

def test_pending_markers(tmp_path):
    write_pending_marker(tmp_path, "id1")
    write_pending_marker(tmp_path, "id2")
    assert sorted(read_pending_markers(tmp_path)) == ["id1", "id2"]
    clear_pending_marker(tmp_path, "id1")
    assert read_pending_markers(tmp_path) == ["id2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_markdown.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.models'`

- [ ] **Step 3: Write `kb/models.py`**

```python
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Fact:
    id: str
    scope: str
    content: str
    tags: list[str] = field(default_factory=list)
    source: str | None = None
    ts: datetime = None  # type: ignore[assignment]
    content_hash: str = ""
    superseded_by: str | None = None
    path: str | None = None
    expires_at: datetime | None = None
```

- [ ] **Step 4: Write `kb/markdown.py`**

```python
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
```

Note: `read_all_facts` indexes curated pages (wiki/decisions without front-matter) as plain-content facts so they are searchable per spec §4. This is intentional and matches the test `test_read_all_facts_collects_memory` (which only checks memory files are present, not that pages are absent).

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_markdown.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/models.py kb-mcp/kb/markdown.py kb-mcp/tests/test_markdown.py
git commit -m "feat(kb): Fact model and markdown serialization"
```

---

## Task 8: Embedder + in-memory test fakes

**Files:**
- Create: `kb-mcp/kb/embeddings.py`
- Create: `kb-mcp/tests/fakes.py`
- Test: `kb-mcp/tests/test_fakes.py`

**Interfaces:**
- Consumes: `Fact` (models.py); `rrf_fuse` (db.py).
- Produces:
  - `Embedder` protocol + `FastEmbedder` (real).
  - `FakeEmbedder` (deterministic word-bag, `dim=64`).
  - `InMemoryVectorStore` implementing the full `VectorStore` protocol (Shared Interfaces) for unit tests.

- [ ] **Step 1: Write the failing test**

`tests/test_fakes.py`:
```python
from datetime import datetime, timezone, timedelta
from tests.fakes import FakeEmbedder, InMemoryVectorStore
from kb.models import Fact
from kb.util import content_hash, make_id

EMB = FakeEmbedder()

def fact(content, scope="global", tags=(), expires_at=None, superseded_by=None):
    ts = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)
    ch = content_hash(content + scope)
    return Fact(id=make_id(ts, ch), scope=scope, content=content, tags=list(tags),
                source=None, ts=ts, content_hash=ch, expires_at=expires_at,
                superseded_by=superseded_by)

def test_fake_embedder_similarity():
    [a, b, c] = EMB.embed(["alpha beta gamma", "alpha beta gamma delta", "zeta eta theta"])
    def cos(u, v): return sum(x*y for x, y in zip(u, v))
    assert cos(a, a) > 0.999
    assert cos(a, b) > cos(a, c)   # near-dup more similar than unrelated

def test_inmemory_nearest_same_scope_only():
    s = InMemoryVectorStore(EMB)
    f1 = fact("alpha beta")
    f2 = fact("alpha beta", scope="project:p")
    s.upsert(f1, EMB.embed([f1.content])[0])
    s.upsert(f2, EMB.embed([f2.content])[0])
    res = s.nearest(EMB.embed(["alpha beta"])[0], scope="global", k=5)
    assert [f.id for f, _ in res] == [f1.id]

def test_inmemory_search_excludes_superseded_and_expired():
    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    s = InMemoryVectorStore(EMB)
    live = fact("alpha beta")
    dead = fact("alpha beta old", superseded_by="someid")
    expired = fact("alpha beta tmp", scope="agent:x:scratch",
                   expires_at=now - timedelta(hours=1))
    for f in (live, dead, expired):
        s.upsert(f, EMB.embed([f.content])[0])
    res = s.search(EMB.embed(["alpha beta"])[0], "alpha beta",
                   scopes=["global", "agent:x:scratch"], tags=None, k=10, now=now)
    ids = [f.id for f, _ in res]
    assert live.id in ids
    assert dead.id not in ids
    assert expired.id not in ids

def test_inmemory_tag_filter():
    s = InMemoryVectorStore(EMB)
    a = fact("alpha", tags=["startup-idea"])
    b = fact("alpha", tags=["life-lesson"])
    s.upsert(a, EMB.embed([a.content])[0]); s.upsert(b, EMB.embed([b.content])[0])
    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    res = s.search(EMB.embed(["alpha"])[0], "alpha", scopes=["global"],
                   tags=["startup-idea"], k=10, now=now)
    assert [f.id for f, _ in res] == [a.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_fakes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.embeddings'`

- [ ] **Step 3: Write `kb/embeddings.py`**

```python
import hashlib
import math
from typing import Protocol


class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    """Real embedder backed by fastembed (ONNX, CPU)."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", dim: int = 384) -> None:
        from fastembed import TextEmbedding  # imported lazily so tests don't need the model
        self.dim = dim
        self._model = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._model.embed(texts)]
```

- [ ] **Step 4: Write `tests/fakes.py`**

```python
import hashlib
import math
from datetime import datetime

from kb.models import Fact
from kb.db import rrf_fuse


class FakeEmbedder:
    """Deterministic word-bag embedder for tests (no model download)."""
    dim = 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.sha1(tok.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


def _cos(u: list[float], v: list[float]) -> float:
    return sum(x * y for x, y in zip(u, v))


class InMemoryVectorStore:
    """Full VectorStore implementation backed by Python dicts (tests only)."""

    def __init__(self, embedder: FakeEmbedder) -> None:
        self._embedder = embedder
        self._rows: dict[str, tuple[Fact, list[float]]] = {}

    def upsert(self, fact: Fact, vector: list[float]) -> None:
        self._rows[fact.id] = (fact, vector)

    def _active(self, now: datetime | None = None):
        for fact, vec in self._rows.values():
            if fact.superseded_by:
                continue
            if now is not None and fact.expires_at is not None and fact.expires_at <= now:
                continue
            yield fact, vec

    def nearest(self, vector: list[float], scope: str, k: int = 1):
        scored = [(f, _cos(vector, vec)) for f, vec in self._active()
                  if f.scope == scope]
        scored.sort(key=lambda fv: -fv[1])
        return scored[:k]

    def search(self, query_vector, query_text, scopes, tags, k, now):
        cands = [(f, vec) for f, vec in self._active(now) if f.scope in scopes]
        if tags:
            tagset = set(tags)
            cands = [(f, vec) for f, vec in cands if tagset & set(f.tags)]
        by_id = {f.id: f for f, _ in cands}
        vector_ranked = [f.id for f, _ in sorted(
            cands, key=lambda fv: -_cos(query_vector, fv[1]))]
        qtokens = set(query_text.lower().split())
        fts_ranked = [f.id for f, _ in sorted(
            cands,
            key=lambda fv: -len(qtokens & set(fv[0].content.lower().split())))]
        fused = rrf_fuse([vector_ranked, fts_ranked])
        return [(by_id[i], score) for i, score in fused[:k]]

    def mark_superseded(self, old_id: str, new_id: str) -> None:
        if old_id in self._rows:
            fact, vec = self._rows[old_id]
            fact.superseded_by = new_id

    def delete_expired_scratch(self, now: datetime) -> int:
        expired = [i for i, (f, _) in self._rows.items()
                   if f.expires_at is not None and f.expires_at <= now]
        for i in expired:
            del self._rows[i]
        return len(expired)

    def clear(self) -> None:
        self._rows.clear()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_fakes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/embeddings.py kb-mcp/tests/fakes.py kb-mcp/tests/test_fakes.py
git commit -m "feat(kb): embedder protocol, FastEmbedder, and test fakes"
```

---

## Task 9: KnowledgeBase.write (dedup, durability, scratch)

**Files:**
- Create: `kb-mcp/kb/store.py`
- Test: `kb-mcp/tests/test_write.py`

**Interfaces:**
- Consumes: `Config`, `Fact`, `Embedder`, `VectorStore`, `DedupConfig`/`decide`, util + markdown helpers.
- Produces: `KnowledgeBase.write(scope, content, tags=None, source=None) -> {"id","path","action"}` (Shared Interfaces). `KnowledgeBase.__init__` accepts an injectable `clock`. (Search is added in Task 10.)

- [ ] **Step 1: Write the failing test**

`tests/test_write.py`:
```python
from datetime import datetime, timezone
from pathlib import Path
import pytest

from kb.config import Config
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def build(tmp_path) -> KnowledgeBase:
    cfg = Config(repo_path=tmp_path, db_url="x")
    return KnowledgeBase(InMemoryVectorStore(FakeEmbedder()), FakeEmbedder(),
                         tmp_path, cfg, clock=lambda: FIXED)

def test_create_writes_markdown_and_indexes(tmp_path):
    kb = build(tmp_path)
    r = kb.write("global", "alpha beta gamma", tags=["ai-trends"], source="conv")
    assert r["action"] == "created"
    assert Path(r["path"]).exists()
    assert "memory/global" in r["path"]
    assert "## [2026-06-18] write | global" in (tmp_path / "log.md").read_text()

def test_exact_duplicate_skipped(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha beta gamma")
    r = kb.write("global", "alpha beta gamma")
    assert r["action"] == "skipped"

def test_near_duplicate_merged_and_supersedes(tmp_path):
    kb = build(tmp_path)
    first = kb.write("global", "alpha beta gamma delta epsilon")
    r = kb.write("global", "alpha beta gamma delta epsilon zeta")
    assert r["action"] == "merged"
    # original marked superseded in the index
    assert kb.store.nearest(FakeEmbedder().embed(["alpha beta gamma delta epsilon"])[0],
                            "global", k=10)  # still returns only active rows
    assert all(f.id != first["id"] for f, _ in kb.store.nearest(
        FakeEmbedder().embed(["alpha"])[0], "global", k=10))

def test_scratch_not_written_to_markdown(tmp_path):
    kb = build(tmp_path)
    r = kb.write("agent:claude:scratch", "ephemeral note here")
    assert r["path"] is None
    assert not (tmp_path / "memory").exists() or not list((tmp_path / "memory").rglob("*.md"))

def test_malformed_scope_rejected(tmp_path):
    kb = build(tmp_path)
    with pytest.raises(ValueError):
        kb.write("nonsense", "x")

def test_markdown_survives_db_down(tmp_path):
    class BrokenStore(InMemoryVectorStore):
        def upsert(self, fact, vector):
            raise RuntimeError("db down")
    cfg = Config(repo_path=tmp_path, db_url="x")
    kb = KnowledgeBase(BrokenStore(FakeEmbedder()), FakeEmbedder(), tmp_path, cfg,
                       clock=lambda: FIXED)
    r = kb.write("global", "alpha beta")
    assert Path(r["path"]).exists()           # truth persisted
    from kb.markdown import read_pending_markers
    assert r["id"] in read_pending_markers(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_write.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.store'`

- [ ] **Step 3: Write `kb/store.py` (write half)**

```python
from datetime import datetime, timezone, timedelta
from typing import Callable

from kb.config import Config
from kb.dedup import DedupConfig, decide
from kb.embeddings import Embedder
from kb.db import VectorStore  # Protocol; added in Task 11 (define stub there if missing)
from kb.markdown import write_fact, append_log, write_pending_marker
from kb.models import Fact
from kb.util import content_hash, make_id, validate_scope, is_scratch


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeBase:
    def __init__(self, store: VectorStore, embedder: Embedder, repo_path,
                 config: Config, clock: Callable[[], datetime] = _utcnow) -> None:
        self.store = store
        self.embedder = embedder
        self.repo_path = repo_path
        self.config = config
        self.clock = clock
        self._dedup = DedupConfig(config.dedup_merge, config.dedup_skip)

    def write(self, scope: str, content: str, tags=None, source=None) -> dict:
        validate_scope(scope)
        tags = list(tags or [])
        ts = self.clock()
        vector = self.embedder.embed([content])[0]

        neighbors = self.store.nearest(vector, scope, k=1)
        best_sim = neighbors[0][1] if neighbors else None
        action = decide(best_sim, self._dedup)

        if action == "skipped":
            existing = neighbors[0][0]
            return {"id": existing.id, "path": existing.path, "action": "skipped"}

        ch = content_hash(content)
        fact = Fact(id=make_id(ts, ch), scope=scope, content=content, tags=tags,
                    source=source, ts=ts, content_hash=ch)

        scratch = is_scratch(scope)
        if scratch:
            fact.expires_at = ts + timedelta(seconds=self.config.scratch_ttl_seconds)
        else:
            write_fact(self.repo_path, fact)  # sets fact.path
            append_log(self.repo_path,
                       f"## [{ts.date().isoformat()}] write | {scope} | {content[:60]}")

        try:
            self.store.upsert(fact, vector)
            if action == "merged" and neighbors:
                self.store.mark_superseded(neighbors[0][0].id, fact.id)
        except Exception:
            if not scratch:
                write_pending_marker(self.repo_path, fact.id)

        return {"id": fact.id, "path": fact.path, "action": action}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_write.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/store.py kb-mcp/tests/test_write.py
git commit -m "feat(kb): KnowledgeBase.write with dedup, scratch, and DB-down resilience"
```

---

## Task 10: KnowledgeBase.search (scope resolution, isolation, tags)

**Files:**
- Modify: `kb-mcp/kb/store.py` (add `search`)
- Test: `kb-mcp/tests/test_search.py`

**Interfaces:**
- Consumes: everything from Task 9.
- Produces: `KnowledgeBase.search(query, scope=None, tags=None, k=8) -> list[dict]`. Default scope `["global"]`; `scope` may be `str` or `list[str]`; results exclude superseded/expired and never cross agent-scratch boundaries.

- [ ] **Step 1: Write the failing test**

`tests/test_search.py`:
```python
from datetime import datetime, timezone
from kb.config import Config
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def build(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    return KnowledgeBase(InMemoryVectorStore(FakeEmbedder()), FakeEmbedder(),
                         tmp_path, cfg, clock=lambda: FIXED)

def test_default_scope_is_global_only(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha beta global fact")
    kb.write("project:p", "alpha beta project fact")
    results = kb.search("alpha beta")
    scopes = {r["scope"] for r in results}
    assert scopes == {"global"}

def test_explicit_scope_list_widens(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha beta global fact")
    kb.write("project:p", "alpha beta project fact")
    results = kb.search("alpha beta", scope=["global", "project:p"])
    assert {r["scope"] for r in results} == {"global", "project:p"}

def test_scratch_isolation_between_agents(tmp_path):
    kb = build(tmp_path)
    kb.write("agent:a:scratch", "alpha beta secret of a")
    # default search (global) must not see it
    assert kb.search("alpha beta") == []
    # agent b's scratch must not see a's
    assert kb.search("alpha beta", scope="agent:b:scratch") == []
    # a's own scratch sees it
    assert any("secret of a" in r["content"]
               for r in kb.search("alpha beta", scope="agent:a:scratch"))

def test_tag_filter(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha idea one", tags=["startup-idea"])
    kb.write("global", "alpha lesson one", tags=["life-lesson"])
    results = kb.search("alpha", tags=["startup-idea"])
    assert len(results) == 1
    assert results[0]["tags"] == ["startup-idea"]

def test_result_dict_shape(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha beta", tags=["x"], source="conv")
    r = kb.search("alpha beta")[0]
    assert set(r.keys()) == {"content", "score", "scope", "tags", "source", "ts", "path"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_search.py -v`
Expected: FAIL with `AttributeError: 'KnowledgeBase' object has no attribute 'search'`

- [ ] **Step 3: Add `search` to `kb/store.py`**

Append this method to the `KnowledgeBase` class:
```python
    def search(self, query: str, scope=None, tags=None, k: int = 8) -> list[dict]:
        if scope is None:
            scopes = ["global"]
        elif isinstance(scope, str):
            scopes = [scope]
        else:
            scopes = list(scope)
        for s in scopes:
            validate_scope(s)

        now = self.clock()
        qvec = self.embedder.embed([query])[0]
        hits = self.store.search(qvec, query, scopes=scopes, tags=tags, k=k, now=now)
        return [
            {
                "content": f.content,
                "score": round(score, 6),
                "scope": f.scope,
                "tags": f.tags,
                "source": f.source,
                "ts": f.ts.isoformat() if f.ts else None,
                "path": f.path,
            }
            for f, score in hits
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_search.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (all tasks so far)

- [ ] **Step 6: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/store.py kb-mcp/tests/test_search.py
git commit -m "feat(kb): KnowledgeBase.search with scope resolution and tag filter"
```

---

## Task 11: PgVectorStore (real backend) + schema

**Files:**
- Modify: `kb-mcp/kb/db.py` (add `VectorStore` protocol + `PgVectorStore` + `connect`)
- Test: `kb-mcp/tests/test_pgvector_integration.py`

**Interfaces:**
- Consumes: `Fact`, `rrf_fuse`.
- Produces: `VectorStore` protocol (Shared Interfaces) and `PgVectorStore(conn, dim)` implementing it; `connect(db_url) -> Connection`; `PgVectorStore.ensure_schema()`.

Note: `kb/store.py` already imports `VectorStore` from `kb.db`. Add the protocol there so that import resolves (it was previously only `rrf_fuse`).

- [ ] **Step 1: Write the failing integration test (skipped without a DB)**

`tests/test_pgvector_integration.py`:
```python
import os
from datetime import datetime, timezone, timedelta
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("KB_TEST_DB_URL"),
    reason="set KB_TEST_DB_URL to run pgvector integration tests",
)

from kb.db import PgVectorStore, connect
from kb.models import Fact
from kb.util import content_hash, make_id
from tests.fakes import FakeEmbedder

EMB = FakeEmbedder()

def fact(content, scope="global", tags=()):
    ts = datetime.now(timezone.utc)
    ch = content_hash(content + scope + str(ts))
    return Fact(id=make_id(ts, ch), scope=scope, content=content, tags=list(tags),
                source=None, ts=ts, content_hash=ch)

@pytest.fixture
def store():
    conn = connect(os.environ["KB_TEST_DB_URL"])
    s = PgVectorStore(conn, dim=EMB.dim)
    s.ensure_schema()
    s.clear()
    yield s
    s.clear()
    conn.close()

def test_upsert_and_search_roundtrip(store):
    f = fact("alpha beta gamma", tags=["ai-trends"])
    store.upsert(f, EMB.embed([f.content])[0])
    now = datetime.now(timezone.utc)
    res = store.search(EMB.embed(["alpha beta"])[0], "alpha beta",
                       scopes=["global"], tags=None, k=5, now=now)
    assert any(rf.id == f.id for rf, _ in res)

def test_supersede_hides_row(store):
    f = fact("alpha beta")
    store.upsert(f, EMB.embed([f.content])[0])
    store.mark_superseded(f.id, "new")
    now = datetime.now(timezone.utc)
    res = store.search(EMB.embed(["alpha beta"])[0], "alpha beta",
                       scopes=["global"], tags=None, k=5, now=now)
    assert all(rf.id != f.id for rf, _ in res)

def test_expired_scratch_excluded_and_swept(store):
    f = fact("alpha beta tmp", scope="agent:x:scratch")
    f.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    store.upsert(f, EMB.embed([f.content])[0])
    now = datetime.now(timezone.utc)
    res = store.search(EMB.embed(["alpha beta"])[0], "alpha beta",
                       scopes=["agent:x:scratch"], tags=None, k=5, now=now)
    assert res == []
    assert store.delete_expired_scratch(now) >= 1
```

- [ ] **Step 2: Run test to verify it is collected and skipped (no DB set)**

Run: `.venv/bin/pytest tests/test_pgvector_integration.py -v`
Expected: `SKIPPED` (reason: set KB_TEST_DB_URL). This proves it imports cleanly.

- [ ] **Step 3: Add `VectorStore` + `PgVectorStore` to `kb/db.py`**

Append below `rrf_fuse`:
```python
from datetime import datetime
from typing import Protocol

import psycopg
from pgvector.psycopg import register_vector

from kb.models import Fact


class VectorStore(Protocol):
    def upsert(self, fact: Fact, vector: list[float]) -> None: ...
    def nearest(self, vector: list[float], scope: str, k: int = 1) -> list[tuple[Fact, float]]: ...
    def search(self, query_vector: list[float], query_text: str, scopes: list[str],
               tags: list[str] | None, k: int, now: datetime) -> list[tuple[Fact, float]]: ...
    def mark_superseded(self, old_id: str, new_id: str) -> None: ...
    def delete_expired_scratch(self, now: datetime) -> int: ...
    def clear(self) -> None: ...


def connect(db_url: str) -> "psycopg.Connection":
    conn = psycopg.connect(db_url, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def _row_to_fact(row) -> Fact:
    (fid, scope, content, tags, source, ts, chash, superseded_by, path, expires_at) = row
    return Fact(id=fid, scope=scope, content=content, tags=list(tags or []),
                source=source, ts=ts, content_hash=chash, superseded_by=superseded_by,
                path=path, expires_at=expires_at)


_COLS = ("id, scope, content, tags, source, ts, content_hash, "
         "superseded_by, path, expires_at")


class PgVectorStore:
    def __init__(self, conn: "psycopg.Connection", dim: int) -> None:
        self.conn = conn
        self.dim = dim

    def ensure_schema(self) -> None:
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS facts (
                id text PRIMARY KEY,
                scope text NOT NULL,
                content text NOT NULL,
                tags text[] NOT NULL DEFAULT '{{}}',
                source text,
                ts timestamptz NOT NULL,
                content_hash text NOT NULL,
                superseded_by text,
                path text,
                expires_at timestamptz,
                embedding vector({self.dim}),
                fts tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS facts_fts_idx ON facts USING gin (fts)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS facts_scope_idx ON facts (scope)")

    def upsert(self, fact: Fact, vector: list[float]) -> None:
        self.conn.execute(
            f"""INSERT INTO facts ({_COLS}, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                  content=EXCLUDED.content, tags=EXCLUDED.tags, source=EXCLUDED.source,
                  ts=EXCLUDED.ts, content_hash=EXCLUDED.content_hash,
                  superseded_by=EXCLUDED.superseded_by, path=EXCLUDED.path,
                  expires_at=EXCLUDED.expires_at, embedding=EXCLUDED.embedding""",
            (fact.id, fact.scope, fact.content, fact.tags, fact.source, fact.ts,
             fact.content_hash, fact.superseded_by, fact.path, fact.expires_at, vector),
        )

    def _active_clause(self) -> str:
        return "superseded_by IS NULL AND (expires_at IS NULL OR expires_at > %(now)s)"

    def nearest(self, vector, scope, k=1):
        from datetime import datetime, timezone
        rows = self.conn.execute(
            f"""SELECT {_COLS}, 1 - (embedding <=> %(v)s) AS sim FROM facts
                WHERE scope = %(scope)s AND superseded_by IS NULL
                ORDER BY embedding <=> %(v)s LIMIT %(k)s""",
            {"v": vector, "scope": scope, "k": k},
        ).fetchall()
        return [(_row_to_fact(r[:-1]), float(r[-1])) for r in rows]

    def search(self, query_vector, query_text, scopes, tags, k, now):
        params = {"v": query_vector, "q": query_text, "scopes": scopes,
                  "now": now, "lim": max(k * 4, 20)}
        tag_clause = ""
        if tags:
            tag_clause = "AND tags && %(tags)s"
            params["tags"] = tags
        base = f"FROM facts WHERE scope = ANY(%(scopes)s) AND {self._active_clause()} {tag_clause}"
        vec_ids = [r[0] for r in self.conn.execute(
            f"SELECT id {base} ORDER BY embedding <=> %(v)s LIMIT %(lim)s", params).fetchall()]
        fts_ids = [r[0] for r in self.conn.execute(
            f"SELECT id {base} AND fts @@ plainto_tsquery('english', %(q)s) "
            f"ORDER BY ts_rank(fts, plainto_tsquery('english', %(q)s)) DESC LIMIT %(lim)s",
            params).fetchall()]
        fused = rrf_fuse([vec_ids, fts_ids])[:k]
        if not fused:
            return []
        order = {fid: i for i, (fid, _) in enumerate(fused)}
        rows = self.conn.execute(
            f"SELECT {_COLS} FROM facts WHERE id = ANY(%s)",
            ([fid for fid, _ in fused],)).fetchall()
        facts = {f.id: f for f in (_row_to_fact(r) for r in rows)}
        return [(facts[fid], score) for fid, score in fused if fid in facts]

    def mark_superseded(self, old_id, new_id):
        self.conn.execute("UPDATE facts SET superseded_by=%s WHERE id=%s", (new_id, old_id))

    def delete_expired_scratch(self, now):
        cur = self.conn.execute(
            "DELETE FROM facts WHERE expires_at IS NOT NULL AND expires_at <= %s", (now,))
        return cur.rowcount

    def clear(self):
        self.conn.execute("TRUNCATE facts")
```

- [ ] **Step 4: Bring up a throwaway pgvector DB and run the integration tests**

```bash
docker run -d --name kb-pg-test -e POSTGRES_PASSWORD=pw -p 55432:5432 pgvector/pgvector:pg16
sleep 5
cd ~/development/knowledge-base/kb-mcp
KB_TEST_DB_URL="postgresql://postgres:pw@127.0.0.1:55432/postgres" .venv/bin/pytest tests/test_pgvector_integration.py -v
docker rm -f kb-pg-test
```
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/db.py kb-mcp/tests/test_pgvector_integration.py
git commit -m "feat(kb): PgVectorStore real backend with hybrid search"
```

---

## Task 12: Reindex (rebuild index from markdown)

**Files:**
- Create: `kb-mcp/kb/reindex.py`
- Test: `kb-mcp/tests/test_reindex.py`

**Interfaces:**
- Consumes: `read_all_facts`, `read_pending_markers`/`clear_pending_marker`, `Embedder`, `VectorStore`, `Config`.
- Produces: `reindex(store, embedder, repo_path, config) -> int` (returns count indexed). Clears the store, re-embeds every markdown fact, upserts, and clears pending markers.

- [ ] **Step 1: Write the failing test**

`tests/test_reindex.py`:
```python
from datetime import datetime, timezone
from kb.config import Config
from kb.reindex import reindex
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def test_reindex_rebuilds_from_markdown(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    # populate via KnowledgeBase (writes markdown)
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)
    kb.write("global", "alpha beta gamma")
    kb.write("project:p", "delta epsilon zeta")

    # fresh empty store; rebuild purely from markdown
    fresh = InMemoryVectorStore(emb)
    count = reindex(fresh, emb, tmp_path, cfg)
    assert count >= 2

    kb2 = KnowledgeBase(fresh, emb, tmp_path, cfg, clock=lambda: FIXED)
    res = kb2.search("alpha beta", scope=["global", "project:p"])
    assert any("alpha beta gamma" in r["content"] for r in res)

def test_reindex_clears_pending_markers(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    from kb.markdown import write_pending_marker, read_pending_markers
    write_pending_marker(tmp_path, "stale-id")
    reindex(InMemoryVectorStore(emb), emb, tmp_path, cfg)
    assert read_pending_markers(tmp_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_reindex.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.reindex'`

- [ ] **Step 3: Write `kb/reindex.py`**

```python
from pathlib import Path

from kb.config import Config
from kb.embeddings import Embedder
from kb.markdown import read_all_facts, read_pending_markers, clear_pending_marker


def reindex(store, embedder: Embedder, repo_path: Path, config: Config) -> int:
    store.clear()
    facts = read_all_facts(repo_path, include_sources=config.index_sources)
    if facts:
        vectors = embedder.embed([f.content for f in facts])
        for fact, vec in zip(facts, vectors):
            store.upsert(fact, vec)
    for marker in read_pending_markers(repo_path):
        clear_pending_marker(repo_path, marker)
    return len(facts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_reindex.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/reindex.py kb-mcp/tests/test_reindex.py
git commit -m "feat(kb): reindex rebuilds the index from markdown"
```

---

## Task 13: MCP server + bearer auth

**Files:**
- Create: `kb-mcp/kb/server.py`
- Test: `kb-mcp/tests/test_auth.py`

**Interfaces:**
- Consumes: `Config`, `KnowledgeBase`, `FastEmbedder`, `PgVectorStore`/`connect`.
- Produces:
  - `BearerAuthMiddleware` (Starlette middleware checking `Authorization: Bearer <KB_MCP_KEY>`; allows `/health` unauthenticated).
  - `build_kb(config) -> KnowledgeBase` (wires real embedder + pgvector).
  - `create_app(config) -> ASGI app` exposing MCP tools `memory_write` / `memory_search` over streamable HTTP, plus `/health`.
  - `app` module-level ASGI app (built from `Config.from_env(os.environ)`) for uvicorn.

- [ ] **Step 1: Write the failing test (auth middleware in isolation)**

`tests/test_auth.py`:
```python
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from kb.server import BearerAuthMiddleware

def build_app(key):
    async def ok(request): return PlainTextResponse("ok")
    async def health(request): return PlainTextResponse("healthy")
    app = Starlette(routes=[Route("/mcp", ok), Route("/health", health)])
    app.add_middleware(BearerAuthMiddleware, key=key)
    return TestClient(app)

def test_missing_token_rejected():
    c = build_app("secret")
    assert c.get("/mcp").status_code == 401

def test_wrong_token_rejected():
    c = build_app("secret")
    assert c.get("/mcp", headers={"Authorization": "Bearer nope"}).status_code == 401

def test_correct_token_allowed():
    c = build_app("secret")
    assert c.get("/mcp", headers={"Authorization": "Bearer secret"}).status_code == 200

def test_health_is_open():
    c = build_app("secret")
    assert c.get("/health").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.server'`

- [ ] **Step 3: Write `kb/server.py`**

```python
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from kb.config import Config
from kb.db import PgVectorStore, connect
from kb.embeddings import FastEmbedder
from kb.store import KnowledgeBase


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, key: str) -> None:
        super().__init__(app)
        self.key = key

    async def dispatch(self, request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[len("Bearer "):] != self.key:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_kb(config: Config) -> KnowledgeBase:
    store = PgVectorStore(connect(config.db_url), dim=config.embed_dim)
    store.ensure_schema()
    embedder = FastEmbedder(model=config.embed_model, dim=config.embed_dim)
    return KnowledgeBase(store, embedder, config.repo_path, config)


def create_app(config: Config):
    from mcp.server.fastmcp import FastMCP

    kb = build_kb(config)
    mcp = FastMCP("kb")

    @mcp.tool()
    def memory_write(scope: str, content: str, tags: list[str] | None = None,
                     source: str | None = None) -> dict:
        """Write a fact to the knowledge base. Returns {id, path, action}."""
        return kb.write(scope, content, tags=tags, source=source)

    @mcp.tool()
    def memory_search(query: str, scope=None, tags: list[str] | None = None,
                      k: int = 8) -> list[dict]:
        """Search the knowledge base. scope: str | list[str] | None (defaults to ['global'])."""
        return kb.search(query, scope=scope, tags=tags, k=k)

    app = mcp.streamable_http_app()

    async def health(request):
        return PlainTextResponse("healthy")

    app.router.routes.append(Route("/health", health))
    app.add_middleware(BearerAuthMiddleware, key=config.mcp_key)
    return app


app = create_app(Config.from_env(os.environ)) if os.environ.get("KB_DB_URL") else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (integration tests skipped without `KB_TEST_DB_URL`)

- [ ] **Step 6: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/server.py kb-mcp/tests/test_auth.py
git commit -m "feat(kb): MCP server with memory tools and bearer auth"
```

---

## Task 14: CLI (`kb reindex`)

**Files:**
- Create: `kb-mcp/kb/cli.py`
- Test: `kb-mcp/tests/test_cli.py`

**Interfaces:**
- Consumes: `Config`, `build_kb`/`connect`/`PgVectorStore`, `FastEmbedder`, `reindex`, `lint` (lint added in Task 17 — Task 14 wires only `reindex`; the `lint` subcommand is added in Task 17).
- Produces: `main(argv=None) -> int` with subcommand `reindex`. Reads config from env.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
from datetime import datetime, timezone
from kb import cli
from kb.config import Config
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def test_reindex_subcommand_uses_injected_kb(tmp_path, monkeypatch, capsys):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    store = InMemoryVectorStore(emb)
    KnowledgeBase(store, emb, tmp_path, cfg, clock=lambda: FIXED).write("global", "alpha beta")

    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    rc = cli.main(["reindex"])
    assert rc == 0
    assert "indexed" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.cli'`

- [ ] **Step 3: Write `kb/cli.py`**

```python
import argparse
import os
import sys

from kb.config import Config
from kb.reindex import reindex


def _load():
    """Build (config, store, embedder) from env. Overridden in tests."""
    from kb.db import PgVectorStore, connect
    from kb.embeddings import FastEmbedder
    cfg = Config.from_env(os.environ)
    store = PgVectorStore(connect(cfg.db_url), dim=cfg.embed_dim)
    store.ensure_schema()
    return cfg, store, FastEmbedder(model=cfg.embed_model, dim=cfg.embed_dim)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kb")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reindex", help="rebuild the index from markdown")
    args = parser.parse_args(argv)

    cfg, store, embedder = _load()
    if args.cmd == "reindex":
        n = reindex(store, embedder, cfg.repo_path, cfg)
        print(f"indexed {n} facts")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/cli.py kb-mcp/tests/test_cli.py
git commit -m "feat(kb): kb CLI with reindex subcommand"
```

---

## Task 15: Docker, compose, env, Makefile

**Files:**
- Create: `kb-mcp/Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `Makefile`

**Interfaces:**
- Consumes: the `kb` package + `kb.server:app`.
- Produces: a runnable stack — `kb-postgres` (pgvector) + `kb-mcp` (HTTP on `127.0.0.1:8077`), on external `hermes-net`; `make` targets `up/down/health/reindex/logs`.

- [ ] **Step 1: Write `kb-mcp/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml /app/
COPY kb /app/kb
RUN pip install --no-cache-dir .
# Pre-download the embedding model into the image cache (optional but avoids first-call latency)
ENV KB_EMBED_MODEL=BAAI/bge-small-en-v1.5
EXPOSE 8077
CMD ["uvicorn", "kb.server:app", "--host", "0.0.0.0", "--port", "8077"]
```

- [ ] **Step 2: Write `.env.example`**

```bash
# kb-mcp configuration
KB_MCP_KEY=          # generate: openssl rand -hex 32
KB_DB_URL=postgresql://kb:kb@kb-postgres:5432/kb
KB_REPO_PATH=/kb
KB_EMBED_MODEL=BAAI/bge-small-en-v1.5
KB_EMBED_DIM=384
KB_DEDUP_MERGE=0.92
KB_DEDUP_SKIP=0.98
KB_SCRATCH_TTL_SECONDS=86400
KB_MCP_PORT=8077
KB_INDEX_SOURCES=false

# postgres
POSTGRES_USER=kb
POSTGRES_PASSWORD=kb
POSTGRES_DB=kb
```

- [ ] **Step 3: Write `docker-compose.yml`**

```yaml
services:
  kb-postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-kb}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-kb}
      POSTGRES_DB: ${POSTGRES_DB:-kb}
    volumes:
      - kb-pgdata:/var/lib/postgresql/data
    networks:
      - hermes-net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-kb}"]
      interval: 5s
      timeout: 3s
      retries: 10

  kb-mcp:
    build: ./kb-mcp
    env_file: .env
    environment:
      KB_REPO_PATH: /kb
    volumes:
      - ./:/kb            # the KB repo (markdown source of truth); ordinary host dir, no flock
    ports:
      - "127.0.0.1:${KB_MCP_PORT:-8077}:8077"
    depends_on:
      kb-postgres:
        condition: service_healthy
    networks:
      - hermes-net

volumes:
  kb-pgdata:              # local-only, never synced (rebuild via `make reindex`)

networks:
  hermes-net:
    external: true
```

- [ ] **Step 4: Write `Makefile`**

```makefile
.DEFAULT_GOAL := help
COMPOSE := docker compose

help:    ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n",$$1,$$2}'

net:     ## ensure the shared external network exists
	docker network inspect hermes-net >/dev/null 2>&1 || docker network create hermes-net

up: net  ## build & start the KB stack
	$(COMPOSE) up -d --build

down:    ## stop the KB stack (pgdata volume survives)
	$(COMPOSE) down

logs:    ## tail kb-mcp logs
	$(COMPOSE) logs -f kb-mcp

health:  ## probe the MCP health endpoint
	curl -fsS http://127.0.0.1:$${KB_MCP_PORT:-8077}/health && echo

reindex: ## rebuild the pgvector index from markdown
	$(COMPOSE) exec kb-mcp kb reindex

lint:    ## run KB health checks (tag drift, index health)
	$(COMPOSE) exec kb-mcp kb lint
```

- [ ] **Step 5: Validate compose config and run a live smoke test**

```bash
cd ~/development/knowledge-base
cp .env.example .env
sed -i '' "s/^KB_MCP_KEY=.*/KB_MCP_KEY=$(openssl rand -hex 32)/" .env
make up
sleep 8
make health                 # expect: healthy
make reindex                # expect: indexed N facts
```
Expected: `healthy` then `indexed N facts`.

- [ ] **Step 6: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/Dockerfile docker-compose.yml .env.example Makefile
git commit -m "feat(kb): docker compose stack, env, and Makefile"
```

---

## Task 16: Register kb-mcp to Claude Code and Hermes; verify bidirectional sharing

**Files:**
- Create: `scripts/smoke-bidirectional.sh`
- Create: `docs/reference/registration.md`

**Interfaces:**
- Consumes: the running stack from Task 15.
- Produces: both tools registered against the same server; a documented bidirectional smoke test.

- [ ] **Step 1: Register with Claude Code (host)**

```bash
KB_KEY=$(grep '^KB_MCP_KEY=' ~/development/knowledge-base/.env | cut -d= -f2)
claude mcp add kb --transport http http://127.0.0.1:8077/mcp \
  --header "Authorization: Bearer ${KB_KEY}"
claude mcp list
```
Expected: `kb` listed and reachable.

- [ ] **Step 2: Register with Hermes (container, via dashboard)**

Document in `docs/reference/registration.md`:
```markdown
# Registering kb-mcp

## Claude Code (host)
claude mcp add kb --transport http http://127.0.0.1:8077/mcp \
  --header "Authorization: Bearer $KB_MCP_KEY"

## Hermes (dashboard at http://127.0.0.1:9119)
Add an MCP server entry:
- transport: http (streamable)
- url: http://kb-mcp:8077/mcp          # reachable over hermes-net
- header: Authorization: Bearer <KB_MCP_KEY>

Both the KB stack and Hermes must share the external docker network `hermes-net`.
Bring the KB stack up with `make up` (it creates/join hermes-net) before starting Hermes,
or ensure hermes-test's compose references the same external network.
```

Apply the Hermes registration via the dashboard (config lives in the named volume; do not edit files in the repo — see hermes-test CLAUDE.md).

- [ ] **Step 3: Write `scripts/smoke-bidirectional.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
KB_KEY=$(grep '^KB_MCP_KEY=' "$(dirname "$0")/../.env" | cut -d= -f2)
BASE=http://127.0.0.1:8077/mcp
# Write via Claude Code's MCP registration:
claude -p 'Use the kb tool memory_write to store: scope "global", content "bidirectional smoke test alpha", tags ["smoke"]. Then confirm the returned action.'
# Read back via a raw search through the same server (proves one shared store):
echo "Now ask Hermes (or a second client) to memory_search 'bidirectional smoke test' and confirm it returns the fact."
```

```bash
chmod +x ~/development/knowledge-base/scripts/smoke-bidirectional.sh
```

- [ ] **Step 4: Run the bidirectional verification**

Write via Claude Code (Step 3 script), then in the Hermes chat (Open WebUI at `http://127.0.0.1:3000` or dashboard) ask it to `memory_search "bidirectional smoke test"`.
Expected: Hermes returns the fact Claude Code wrote — proving one shared store, both directions.

- [ ] **Step 5: Commit**

```bash
cd ~/development/knowledge-base
git add scripts/smoke-bidirectional.sh docs/reference/registration.md
git commit -m "feat(kb): register to both clients and bidirectional smoke test"
```

---

## Task 17: `kb lint` (tag drift + index health)

**Files:**
- Create: `kb-mcp/kb/lint.py`
- Modify: `kb-mcp/kb/cli.py` (add `lint` subcommand)
- Test: `kb-mcp/tests/test_lint.py`

**Interfaces:**
- Consumes: `read_all_facts`, `read_pending_markers`, `Config`.
- Produces: `lint_report(repo_path, config) -> dict` with keys `tag_drift` (list of `(a, b)` near-duplicate tag pairs) and `pending_reindex` (list of ids). `cli.main(["lint"])` prints the report and returns non-zero if any issues.

- [ ] **Step 1: Write the failing test**

`tests/test_lint.py`:
```python
from datetime import datetime, timezone
from kb.config import Config
from kb.lint import lint_report
from kb.store import KnowledgeBase
from kb.markdown import write_pending_marker
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def build(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED), cfg

def test_detects_tag_drift(tmp_path):
    kb, cfg = build(tmp_path)
    kb.write("global", "one", tags=["ai-trends"])
    kb.write("global", "two", tags=["ai-trend"])     # near-duplicate of ai-trends
    report = lint_report(tmp_path, cfg)
    pairs = {frozenset(p) for p in report["tag_drift"]}
    assert frozenset({"ai-trend", "ai-trends"}) in pairs

def test_no_false_positive_for_distinct_tags(tmp_path):
    kb, cfg = build(tmp_path)
    kb.write("global", "one", tags=["startup-idea"])
    kb.write("global", "two", tags=["life-lesson"])
    assert lint_report(tmp_path, cfg)["tag_drift"] == []

def test_reports_pending_reindex(tmp_path):
    _, cfg = build(tmp_path)
    write_pending_marker(tmp_path, "stuck-id")
    assert lint_report(tmp_path, cfg)["pending_reindex"] == ["stuck-id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_lint.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.lint'`

- [ ] **Step 3: Write `kb/lint.py`**

```python
from pathlib import Path

from kb.config import Config
from kb.markdown import read_all_facts, read_pending_markers


def _normalize(tag: str) -> str:
    return tag.lower().replace("-", "").replace("_", "").rstrip("s")


def lint_report(repo_path: Path, config: Config) -> dict:
    facts = read_all_facts(repo_path, include_sources=False)
    tags = sorted({t for f in facts for t in f.tags})

    drift: list[tuple[str, str]] = []
    for i in range(len(tags)):
        for j in range(i + 1, len(tags)):
            a, b = tags[i], tags[j]
            if a != b and _normalize(a) == _normalize(b):
                drift.append((a, b))

    return {
        "tag_drift": drift,
        "pending_reindex": sorted(read_pending_markers(repo_path)),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_lint.py -v`
Expected: PASS

- [ ] **Step 5: Add the `lint` subcommand to `kb/cli.py`**

In `main()`, after `sub.add_parser("reindex", ...)` add:
```python
    sub.add_parser("lint", help="health-check tags and index state")
```
And after the `reindex` branch, before `return 1`, add:
```python
    if args.cmd == "lint":
        from kb.lint import lint_report
        report = lint_report(cfg.repo_path, cfg)
        for a, b in report["tag_drift"]:
            print(f"tag-drift: {a!r} ~ {b!r}")
        for fid in report["pending_reindex"]:
            print(f"pending-reindex: {fid}")
        issues = len(report["tag_drift"]) + len(report["pending_reindex"])
        print(f"{issues} issue(s)")
        return 1 if issues else 0
```

- [ ] **Step 6: Add a CLI lint test**

Append to `tests/test_cli.py`:
```python
def test_lint_subcommand(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from kb.store import KnowledgeBase
    from tests.fakes import FakeEmbedder, InMemoryVectorStore
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    store = InMemoryVectorStore(emb)
    kb = KnowledgeBase(store, emb, tmp_path, cfg, clock=lambda: __import__("datetime").datetime(2026,6,18,tzinfo=__import__("datetime").timezone.utc))
    kb.write("global", "a", tags=["ai-trends"]); kb.write("global", "b", tags=["ai-trend"])
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    rc = cli.main(["lint"])
    assert rc == 1
    assert "tag-drift" in capsys.readouterr().out
```

- [ ] **Step 7: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: PASS (integration skipped)

- [ ] **Step 8: Commit**

```bash
cd ~/development/knowledge-base
git add kb-mcp/kb/lint.py kb-mcp/kb/cli.py kb-mcp/tests/test_lint.py kb-mcp/tests/test_cli.py
git commit -m "feat(kb): kb lint for tag drift and index health"
```

---

## Task 18: kb-ingest skill

**Files:**
- Create: `~/.claude/skills/kb-ingest/SKILL.md`
- Create: `docs/reference/kb-ingest.snapshot.md` (repo copy for versioning)

**Interfaces:**
- Consumes: the `memory_write` tool and the `sources/`, `wiki/`, `index.md`, `log.md` layout.
- Produces: a reusable Claude Code skill implementing the Karpathy ingest loop.

- [ ] **Step 1: Write `~/.claude/skills/kb-ingest/SKILL.md`**

```markdown
---
name: kb-ingest
description: Use when the user drops a transcript/source into the KB sources/ folder or asks to "ingest", "add to the knowledge base", or "compile notes". Distills raw sources into tagged atomic facts + a curated wiki page.
---

# KB Ingest (Karpathy loop)

## Inputs
- A raw file under `~/development/knowledge-base/sources/` (or pasted content).

## Steps
1. **Read** the source. Do NOT dump it wholesale into memory_write.
2. **Distill** 3–10 atomic, standalone facts. For each, call the `kb` tool
   `memory_write(scope, content, tags, source)`:
   - `scope`: `global` for general knowledge; `project:<name>` if clearly project-bound.
   - `tags`: free-form topic tags (`ai-trends`, `startup-idea`, `business-idea`,
     `life-lesson`, `radar`, ...). Reuse existing tags; check with `memory_search` first.
   - `source`: the source filename.
3. **Synthesise** into the matching `wiki/<topic>.md` page: create it if missing,
   otherwise integrate (don't append blindly — merge, cross-reference, supersede stale lines).
4. **Update** `wiki/index.md` if a new topic page was created.
5. **Log** one line to `log.md`: `## [YYYY-MM-DD] ingest | <source> | <summary>`.
6. **Report** what was written (facts + which wiki page) and any `kb lint` follow-ups.

## Rules
- Raw `sources/` files are immutable — never edit them.
- Prefer reusing an existing tag over coining a near-duplicate (avoid `ai-trend` vs `ai-trends`).
- One topic per wiki page; keep pages human-browsable.
```

- [ ] **Step 2: Snapshot into the repo**

```bash
mkdir -p ~/development/knowledge-base/docs/reference
cp ~/.claude/skills/kb-ingest/SKILL.md ~/development/knowledge-base/docs/reference/kb-ingest.snapshot.md
```

- [ ] **Step 3: Verify the skill is discovered**

Run: `claude -p "List my available skills."` (or start a session and check)
Expected: `kb-ingest` appears.

- [ ] **Step 4: Functional check**

Drop a small file into `sources/` and ask Claude to ingest it; confirm facts appear via `memory_search` and a `wiki/<topic>.md` page is created/updated.

- [ ] **Step 5: Commit**

```bash
cd ~/development/knowledge-base
git add docs/reference/kb-ingest.snapshot.md
git commit -m "feat(kb): kb-ingest skill (Karpathy ingest loop)"
```

---

## Task 19: Durability wiring (git remote + Drive mirror + rebuild check)

**Files:**
- Create: `scripts/mirror-to-drive.sh`
- Create: `docs/reference/durability.md`
- Create: `~/Library/LaunchAgents/dev.kb.mirror.plist` (macOS schedule)

**Interfaces:**
- Consumes: the KB repo, `make reindex`.
- Produces: a private git remote, a scheduled one-way Drive mirror (excluding `.git/` and infra), and a verified rebuild-from-markdown path.

- [ ] **Step 1: Add a private git remote and push**

```bash
cd ~/development/knowledge-base
# create a PRIVATE repo first (gh or web), then:
git remote add origin git@github.com:<you>/knowledge-base-private.git
git push -u origin HEAD
```
Expected: push succeeds to a private remote.

- [ ] **Step 2: Write `scripts/mirror-to-drive.sh`**

```bash
#!/usr/bin/env bash
# One-way mirror of the markdown KB into Google Drive. Excludes git + infra.
# Requires: rclone configured with a remote named "gdrive".
set -euo pipefail
SRC="$HOME/development/knowledge-base"
DEST="gdrive:knowledge-base"
rclone copy "$SRC" "$DEST" \
  --exclude ".git/**" \
  --exclude "kb-mcp/**" \
  --exclude ".venv/**" \
  --exclude ".env" \
  --exclude "docker-compose.yml" \
  --exclude "Makefile" \
  --exclude ".kb/**" \
  --create-empty-src-dirs
echo "mirrored $SRC -> $DEST"
```

```bash
chmod +x ~/development/knowledge-base/scripts/mirror-to-drive.sh
```

- [ ] **Step 3: Dry-run the mirror**

```bash
rclone copy "$HOME/development/knowledge-base" gdrive:knowledge-base \
  --exclude ".git/**" --exclude "kb-mcp/**" --exclude ".venv/**" --dry-run
```
Expected: lists markdown files to copy; **no** `.git/` or `kb-mcp/` entries.

- [ ] **Step 4: Write the launchd schedule (hourly)**

`~/Library/LaunchAgents/dev.kb.mirror.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>dev.kb.mirror</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>$HOME/development/knowledge-base/scripts/mirror-to-drive.sh</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>/tmp/kb-mirror.log</string>
  <key>StandardErrorPath</key><string>/tmp/kb-mirror.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/dev.kb.mirror.plist
```

- [ ] **Step 5: Write `docs/reference/durability.md`**

```markdown
# Durability & Recovery

- **Truth:** markdown in this repo. **Index:** pgvector (disposable).
- **Primary backup:** private git remote (`origin`). Commit + push regularly.
- **Secondary:** one-way Drive mirror via `scripts/mirror-to-drive.sh` (hourly launchd).
  Excludes `.git/` and infra to avoid corruption and churn.

## Restore / new machine
1. `git clone <private remote> ~/development/knowledge-base`
2. `cd ~/development/knowledge-base && make up`
3. `make reindex`   # rebuilds pgvector from markdown
4. `make health`    # healthy
The DB is never restored from backup — it is always rebuilt from markdown.
```

- [ ] **Step 6: Verify the rebuild path (the core durability guarantee)**

```bash
cd ~/development/knowledge-base
make down
docker volume rm knowledge-base_kb-pgdata    # wipe the index entirely
make up && sleep 8
make reindex                                  # rebuild from markdown
# confirm a known fact is searchable again (via Claude Code or smoke script)
```
Expected: `make reindex` reports the fact count and search returns previously-written facts — proving zero data loss with no DB backup.

- [ ] **Step 7: Commit**

```bash
cd ~/development/knowledge-base
git add scripts/mirror-to-drive.sh docs/reference/durability.md
git commit -m "feat(kb): durability wiring (git remote + Drive mirror + rebuild check)"
git push
```

---

## Self-Review

**1. Spec coverage:**
- §2 sharing model / one MCP → Tasks 13, 16. ✓
- §2 markdown source of truth → Tasks 1, 7. ✓
- §2 pgvector local container → Tasks 11, 15. ✓
- §2 durability (git remote + Drive mirror, DB rebuild-only) → Task 19. ✓
- §2 local embeddings (bge-small, 384) → Task 8 (FastEmbedder), Task 3 (Config). ✓
- §2 Python / HTTP transport → Tasks 3, 13, 15. ✓
- §4 repo layout (context/wiki/decisions/sources/memory + index/log) → Task 1. ✓
- §4 channels distinct; memory_write only writes memory/; sources not indexed → Tasks 7, 9, 12. ✓
- §5 contract memory_write/memory_search + return shapes → Tasks 9, 10, 13. ✓
- §5 two axes (scope + tags) → Tasks 4 (scope), 9/10 (tags). ✓
- §5 scopes + default ["global"] + scratch ephemeral → Tasks 4, 9, 10. ✓
- §6 write flow (dedup, markdown-first, supersede, pending marker) → Task 9. ✓
- §6 search flow (hybrid + RRF + filters) → Tasks 6, 10, 11. ✓
- §6 reindex → Task 12, CLI Task 14. ✓
- §7 deployment (compose, hermes-net external, 127.0.0.1, bind-mount KB) → Task 15. ✓
- §7 Claude Code + Hermes registration → Tasks 2, 16. ✓
- §7 security (bearer auth, localhost) → Task 13. ✓
- §8 error handling (model fail-fast, DB-down resilience, malformed scope) → Tasks 9, 13. ✓
- §9 all five TDD targets → Tasks 5/9 (dedup), 10 (scope isolation + tags), 6/11 (RRF determinism), 9 (DB-down), 12 (reindex reproducibility). ✓
- §10 build order incl. kb-ingest + kb lint → Tasks 17, 18. ✓

No gaps found.

**2. Placeholder scan:** No `TBD`/`TODO`/"handle edge cases"/"similar to Task N". Every code step shows complete code. ✓

**3. Type consistency:** `Config`, `Fact`, `Embedder`, `VectorStore`, `KnowledgeBase.write/search`, `rrf_fuse`, `reindex`, `lint_report`, `BearerAuthMiddleware`, `create_app/build_kb`, `_load` all match the Shared Interfaces block and their cross-task uses (e.g., `store.search(..., now=...)` signature is identical in fakes, PgVectorStore, and KnowledgeBase.search). ✓
