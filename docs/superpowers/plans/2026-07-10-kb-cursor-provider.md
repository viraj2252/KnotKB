# Cursor SDK LLM Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cursor` as a selectable LLM provider so a work KnotKB instance can use a Cursor API key for `ask`, entity extract, and auto-ingest.

**Architecture:** A new `CursorAgentClient` implements the existing `LLMClient` protocol (`complete(messages, model) -> str`) by flattening messages into one prompt and running a one-shot `cursor_sdk.Agent.prompt()` against an empty throwaway workspace. A `build_llm(config)` factory in `kb/synth.py` centralizes the provider decision for both the CLI and the MCP server. `cursor-sdk` ships as an optional extra installed via a `KB_EXTRAS` Docker build arg.

**Tech Stack:** Python 3.12, pytest, cursor-sdk >= 0.1.9 (`from cursor_sdk import Agent, AgentOptions, LocalAgentOptions`; one-shot result text is `result.result`), Docker Compose.

**Spec:** `docs/superpowers/specs/2026-07-10-kb-cursor-provider-design.md`

## Global Constraints

- All work happens in `kb-mcp/` (package) plus root deployment/docs files; run tests from `kb-mcp/` with `.venv/bin/pytest`.
- `KB_SYNTH_PROVIDER` values: `openai` (default) | `cursor`. Provider `openai` behavior must be byte-for-byte unchanged.
- `synth_configured`: `openai` → `bool(synth_base_url)`; `cursor` → `bool(cursor_api_key)`.
- The Cursor agent must never see real files: local runtime cwd is a fresh `tempfile.mkdtemp(prefix="kb-cursor-")` dir.
- The `cursor_sdk` import is lazy (inside `CursorAgentClient.__init__`); a missing package raises `RuntimeError` mentioning `KB_EXTRAS=cursor` and `pip install 'kb-mcp[cursor]'`.
- Tests never require the real `cursor-sdk` package or a live key — fake the module via `sys.modules`.
- Every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Config fields for provider selection

**Files:**
- Modify: `kb-mcp/kb/config.py` (dataclass fields after `synth_max_facts`; `from_env` after the `synth_max_facts` line)
- Test: `kb-mcp/tests/test_config.py`

**Interfaces:**
- Produces: `Config.synth_provider: str = "openai"`, `Config.cursor_api_key: str = ""`, parsed from `KB_SYNTH_PROVIDER` / `CURSOR_API_KEY`.

- [ ] **Step 1: Write the failing tests** — append to `kb-mcp/tests/test_config.py`:

```python
def test_provider_defaults_to_openai(tmp_path):
    cfg = Config.from_env({"KB_REPO_PATH": str(tmp_path), "KB_DB_URL": "x"})
    assert cfg.synth_provider == "openai"
    assert cfg.cursor_api_key == ""

def test_provider_cursor_parsed(tmp_path):
    cfg = Config.from_env({"KB_REPO_PATH": str(tmp_path), "KB_DB_URL": "x",
                           "KB_SYNTH_PROVIDER": " Cursor ",
                           "CURSOR_API_KEY": "crsr_test"})
    assert cfg.synth_provider == "cursor"
    assert cfg.cursor_api_key == "crsr_test"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd kb-mcp && .venv/bin/pytest tests/test_config.py -q`
Expected: 2 FAIL (`AttributeError`/`TypeError`: no field `synth_provider`)

- [ ] **Step 3: Implement** — in `kb-mcp/kb/config.py`, add dataclass fields directly under `synth_max_facts: int = 8`:

```python
    synth_provider: str = "openai"
    cursor_api_key: str = ""
```

and in `from_env`, directly under the `synth_max_facts=...` line:

```python
            synth_provider=env.get("KB_SYNTH_PROVIDER", "openai").strip().lower(),
            cursor_api_key=env.get("CURSOR_API_KEY", ""),
```

- [ ] **Step 4: Run to verify pass**

Run: `cd kb-mcp && .venv/bin/pytest tests/test_config.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add kb-mcp/kb/config.py kb-mcp/tests/test_config.py
git commit -m "feat(kb): config knobs for LLM provider selection (KB_SYNTH_PROVIDER, CURSOR_API_KEY)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: CursorAgentClient adapter

**Files:**
- Create: `kb-mcp/kb/cursor_llm.py`
- Test: `kb-mcp/tests/test_cursor_llm.py` (new)

**Interfaces:**
- Consumes: `Config.cursor_api_key` (Task 1); `LLMClient` protocol from `kb/synth.py` (`complete(messages: list[dict], model: str) -> str`).
- Produces: `CursorAgentClient(api_key: str, workspace: str | None = None)` with `.complete(messages, model) -> str`; raises `RuntimeError` in `__init__` when `cursor_sdk` is missing.

- [ ] **Step 1: Write the failing tests** — create `kb-mcp/tests/test_cursor_llm.py`:

```python
import sys
import types

import pytest


def install_fake_cursor_sdk(monkeypatch, reply="fake answer"):
    """Install a fake cursor_sdk module; returns the list of recorded calls."""
    calls = []
    mod = types.ModuleType("cursor_sdk")

    class LocalAgentOptions:
        def __init__(self, cwd=None):
            self.cwd = cwd

    class AgentOptions:
        def __init__(self, model=None, api_key=None, local=None):
            self.model, self.api_key, self.local = model, api_key, local

    class _Result:
        def __init__(self, text):
            self.result = text

    class Agent:
        @staticmethod
        def prompt(prompt, options):
            calls.append((prompt, options))
            return _Result(reply)

    mod.Agent, mod.AgentOptions, mod.LocalAgentOptions = Agent, AgentOptions, LocalAgentOptions
    monkeypatch.setitem(sys.modules, "cursor_sdk", mod)
    return calls


MESSAGES = [{"role": "system", "content": "You answer strictly from context."},
            {"role": "user", "content": "Question: what is alpha?"}]


def test_complete_flattens_messages_and_returns_text(monkeypatch):
    calls = install_fake_cursor_sdk(monkeypatch, reply="alpha is [1]")
    from kb.cursor_llm import CursorAgentClient
    out = CursorAgentClient("crsr_k").complete(MESSAGES, "composer-2.5")
    assert out == "alpha is [1]"
    prompt, options = calls[0]
    assert "You answer strictly from context." in prompt
    assert "Question: what is alpha?" in prompt


def test_complete_prepends_no_tools_instruction(monkeypatch):
    calls = install_fake_cursor_sdk(monkeypatch)
    from kb.cursor_llm import CursorAgentClient
    CursorAgentClient("crsr_k").complete(MESSAGES, "composer-2.5")
    prompt, _ = calls[0]
    assert prompt.startswith("Answer directly as plain text.")
    assert "Do not use any tools" in prompt


def test_complete_passes_model_key_and_empty_workspace(monkeypatch, tmp_path):
    calls = install_fake_cursor_sdk(monkeypatch)
    from kb.cursor_llm import CursorAgentClient
    ws = tmp_path / "empty"
    ws.mkdir()
    CursorAgentClient("crsr_k", workspace=str(ws)).complete(MESSAGES, "composer-2.5")
    _, options = calls[0]
    assert options.model == "composer-2.5"
    assert options.api_key == "crsr_k"
    assert options.local.cwd == str(ws)


def test_default_workspace_is_fresh_temp_dir(monkeypatch):
    install_fake_cursor_sdk(monkeypatch)
    from kb.cursor_llm import CursorAgentClient
    import os
    c = CursorAgentClient("crsr_k")
    assert os.path.isdir(c.workspace)
    assert os.listdir(c.workspace) == []
    assert "kb-cursor-" in c.workspace


def test_missing_sdk_raises_actionable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "cursor_sdk", None)  # import -> ImportError
    from kb.cursor_llm import CursorAgentClient
    with pytest.raises(RuntimeError) as ei:
        CursorAgentClient("crsr_k")
    msg = str(ei.value)
    assert "KB_EXTRAS=cursor" in msg
    assert "kb-mcp[cursor]" in msg
```

- [ ] **Step 2: Run to verify failure**

Run: `cd kb-mcp && .venv/bin/pytest tests/test_cursor_llm.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb.cursor_llm'`

- [ ] **Step 3: Implement** — create `kb-mcp/kb/cursor_llm.py`:

```python
import tempfile

_INSTRUCTION = (
    "Answer directly as plain text. Do not use any tools, do not read or "
    "modify files, do not run commands. Respond with the answer only."
)


class CursorAgentClient:
    """LLMClient adapter: one-shot Cursor agent runs via cursor-sdk.

    Each complete() is a stateless Agent.prompt() with a local runtime whose
    cwd is an empty throwaway directory, so the agent never sees real files.
    """

    def __init__(self, api_key: str, workspace: str | None = None) -> None:
        try:
            from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
        except ImportError as e:
            raise RuntimeError(
                "cursor provider selected but cursor-sdk is not installed. "
                "Rebuild the image with KB_EXTRAS=cursor "
                "(or: pip install 'kb-mcp[cursor]')."
            ) from e
        self._agent = Agent
        self._agent_options = AgentOptions
        self._local_options = LocalAgentOptions
        self.api_key = api_key
        self.workspace = workspace or tempfile.mkdtemp(prefix="kb-cursor-")

    def complete(self, messages: list[dict], model: str) -> str:
        prompt = "\n\n".join([_INSTRUCTION] + [m["content"] for m in messages])
        result = self._agent.prompt(
            prompt,
            self._agent_options(
                model=model,
                api_key=self.api_key,
                local=self._local_options(cwd=self.workspace),
            ),
        )
        return result.result
```

- [ ] **Step 4: Run to verify pass**

Run: `cd kb-mcp && .venv/bin/pytest tests/test_cursor_llm.py -q`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add kb-mcp/kb/cursor_llm.py kb-mcp/tests/test_cursor_llm.py
git commit -m "feat(kb): CursorAgentClient — LLMClient adapter over one-shot cursor-sdk runs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Provider-aware synth_configured + build_llm factory

**Files:**
- Modify: `kb-mcp/kb/synth.py` (the `synth_configured` function; add `build_llm` below it)
- Test: `kb-mcp/tests/test_synth.py`

**Interfaces:**
- Consumes: `Config.synth_provider`/`cursor_api_key` (Task 1); `CursorAgentClient` (Task 2); existing `OpenAIWireClient`.
- Produces: `build_llm(config) -> LLMClient | None` — `None` when unconfigured; `CursorAgentClient(config.cursor_api_key)` for provider `cursor`; `OpenAIWireClient(config.synth_base_url, config.synth_key)` otherwise. `synth_configured(config) -> bool` per Global Constraints.

- [ ] **Step 1: Write the failing tests** — append to `kb-mcp/tests/test_synth.py` (module already imports `Config`; reuse the fake-SDK installer):

```python
def test_synth_configured_cursor_requires_key(tmp_path):
    from kb.synth import synth_configured
    with_key = Config(repo_path=tmp_path, db_url="x", synth_provider="cursor",
                      cursor_api_key="crsr_k", synth_base_url="")
    without_key = Config(repo_path=tmp_path, db_url="x", synth_provider="cursor",
                         synth_base_url="http://claude-proxy:8000/v1")
    assert synth_configured(with_key) is True
    assert synth_configured(without_key) is False

def test_build_llm_openai(tmp_path):
    from kb.synth import build_llm, OpenAIWireClient
    cfg = Config(repo_path=tmp_path, db_url="x")
    assert isinstance(build_llm(cfg), OpenAIWireClient)

def test_build_llm_unconfigured_returns_none(tmp_path):
    from kb.synth import build_llm
    assert build_llm(Config(repo_path=tmp_path, db_url="x", synth_base_url="")) is None

def test_build_llm_cursor(tmp_path, monkeypatch):
    from tests.test_cursor_llm import install_fake_cursor_sdk
    install_fake_cursor_sdk(monkeypatch)
    from kb.synth import build_llm
    from kb.cursor_llm import CursorAgentClient
    cfg = Config(repo_path=tmp_path, db_url="x", synth_provider="cursor",
                 cursor_api_key="crsr_k")
    llm = build_llm(cfg)
    assert isinstance(llm, CursorAgentClient)
    assert llm.api_key == "crsr_k"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd kb-mcp && .venv/bin/pytest tests/test_synth.py -q`
Expected: new tests FAIL (`cursor` config still truthy via base URL default → `synth_configured` wrong; `ImportError: cannot import name 'build_llm'`)

- [ ] **Step 3: Implement** — in `kb-mcp/kb/synth.py`, replace `synth_configured` and add `build_llm` beneath it:

```python
def synth_configured(config) -> bool:
    """LLM features are enabled iff the selected provider has credentials."""
    if config.synth_provider == "cursor":
        return bool(config.cursor_api_key)
    return bool(config.synth_base_url)


def build_llm(config) -> "LLMClient | None":
    """Construct the configured LLM client, or None when LLM features are off."""
    if not synth_configured(config):
        return None
    if config.synth_provider == "cursor":
        from kb.cursor_llm import CursorAgentClient
        return CursorAgentClient(config.cursor_api_key)
    return OpenAIWireClient(config.synth_base_url, config.synth_key)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd kb-mcp && .venv/bin/pytest tests/test_synth.py -q`
Expected: all PASS (including pre-existing `synth_configured` tests)

- [ ] **Step 5: Commit**

```bash
git add kb-mcp/kb/synth.py kb-mcp/tests/test_synth.py
git commit -m "feat(kb): build_llm factory + provider-aware synth_configured

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Wire CLI and server through build_llm

**Files:**
- Modify: `kb-mcp/kb/cli.py` (`_llm`, `_LLM_DISABLED_HINT`)
- Modify: `kb-mcp/kb/server.py` (the `ask` tool body)
- Test: `kb-mcp/tests/test_cli.py`

**Interfaces:**
- Consumes: `build_llm` / `synth_configured` (Task 3).
- Produces: no new interfaces — behavior change only. `cli._llm(cfg)` returns whatever `build_llm(cfg)` returns (tests still monkeypatch `cli._llm`).

- [ ] **Step 1: Write the failing tests** — append to `kb-mcp/tests/test_cli.py`:

```python
def test_llm_builds_cursor_client_when_provider_cursor(tmp_path, monkeypatch):
    from tests.test_cursor_llm import install_fake_cursor_sdk
    install_fake_cursor_sdk(monkeypatch)
    from kb.cursor_llm import CursorAgentClient
    cfg = Config(repo_path=tmp_path, db_url="x", synth_provider="cursor",
                 cursor_api_key="crsr_k")
    assert isinstance(cli._llm(cfg), CursorAgentClient)

def test_extract_hint_mentions_cursor_option(tmp_path, monkeypatch, capsys):
    cfg = Config(repo_path=tmp_path, db_url="x", synth_base_url="")
    emb = FakeEmbedder(); store = InMemoryVectorStore(emb)
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    assert cli.main(["extract"]) == 1
    out = capsys.readouterr().out
    assert "KB_SYNTH_BASE_URL" in out and "KB_SYNTH_PROVIDER=cursor" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `cd kb-mcp && .venv/bin/pytest tests/test_cli.py -q`
Expected: the two new tests FAIL (`_llm` still constructs `OpenAIWireClient`... for cursor cfg `synth_base_url` defaults truthy → wrong type; hint lacks cursor wording)

- [ ] **Step 3: Implement** — in `kb-mcp/kb/cli.py`, replace `_LLM_DISABLED_HINT` and `_llm`:

```python
_LLM_DISABLED_HINT = ("LLM disabled: set KB_SYNTH_BASE_URL, or KB_SYNTH_PROVIDER=cursor "
                      "with CURSOR_API_KEY (see docs/SETUP.md)")


def _llm(cfg):
    from kb.synth import build_llm
    return build_llm(cfg)
```

In `kb-mcp/kb/server.py`, replace the `ask` tool body:

```python
    @mcp.tool()
    def ask(question: str, scope=None, k: int | None = None) -> dict:
        """Answer a question from the KB with cited sources. Returns {answer, citations, used_facts}."""
        from kb.synth import synthesize, synth_configured, build_llm
        if not synth_configured(config):
            return {"error": "LLM synthesis not configured: set KB_SYNTH_BASE_URL, "
                             "or KB_SYNTH_PROVIDER=cursor with CURSOR_API_KEY "
                             "(see docs/SETUP.md, LLM backend section)"}
        return synthesize(kb, question, build_llm(config), scope=scope, k=k)
```

- [ ] **Step 4: Run the full suite**

Run: `cd kb-mcp && .venv/bin/pytest -q`
Expected: all PASS, 3 skipped (pgvector integration). Pre-existing hint tests (`"KB_SYNTH_BASE_URL" in out`) still pass with the new wording.

- [ ] **Step 5: Commit**

```bash
git add kb-mcp/kb/cli.py kb-mcp/kb/server.py kb-mcp/tests/test_cli.py
git commit -m "feat(kb): route CLI and ask tool through build_llm; provider-aware hints

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Packaging + deployment (extra, build arg, env docs)

**Files:**
- Modify: `kb-mcp/pyproject.toml` (`[project.optional-dependencies]`)
- Modify: `kb-mcp/Dockerfile` (ARG + pip install line)
- Modify: `docker-compose.yml` (`kb-mcp.build` block)
- Modify: `.env.example` (LLM section)

**Interfaces:**
- Consumes: nothing from code tasks.
- Produces: `pip install 'kb-mcp[cursor]'` extra; `KB_EXTRAS` build arg honored by compose (`.env` interpolation).

- [ ] **Step 1: pyproject extra** — in `kb-mcp/pyproject.toml` replace the optional-dependencies block:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0"]
cursor = ["cursor-sdk>=0.1.9"]
```

- [ ] **Step 2: Dockerfile build arg** — in `kb-mcp/Dockerfile`, replace the single `RUN pip install --no-cache-dir .` line with:

```dockerfile
ARG KB_EXTRAS=
RUN if [ -n "$KB_EXTRAS" ]; then pip install --no-cache-dir ".[${KB_EXTRAS}]"; \
    else pip install --no-cache-dir .; fi
```

- [ ] **Step 3: compose build args** — in `docker-compose.yml`, replace `build: ./kb-mcp` with:

```yaml
    build:
      context: ./kb-mcp
      args:
        KB_EXTRAS: ${KB_EXTRAS:-}
```

(`KB_SYNTH_PROVIDER` and `CURSOR_API_KEY` need no compose changes — `env_file: .env` already forwards any values set there.)

- [ ] **Step 4: .env.example** — in the LLM section, directly under the `KB_SYNTH_BASE_URL=` line block, add:

```dotenv
# Provider: openai = any OpenAI-wire endpoint above; cursor = Cursor SDK
# (work subscriptions). cursor requires CURSOR_API_KEY and an image built
# with KB_EXTRAS=cursor.
KB_SYNTH_PROVIDER=openai
CURSOR_API_KEY=
# Comma-separated pip extras baked into the kb-mcp image at build time.
# Set to "cursor" for the Cursor provider, then `make up` to rebuild.
KB_EXTRAS=
```

- [ ] **Step 5: Verify configs statically**

Run (repo root): `docker compose config -q && docker compose -f docker-compose.yml -f docker-compose.hermes.yml config -q && echo OK`
Expected: `OK`
Run: `cd kb-mcp && .venv/bin/python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['optional-dependencies']['cursor'])"`
Expected: `['cursor-sdk>=0.1.9']`

- [ ] **Step 6: Verify the default image still builds** (no extras — must not regress home installs)

Run: `docker compose build kb-mcp`
Expected: build succeeds

- [ ] **Step 7: Commit**

```bash
git add kb-mcp/pyproject.toml kb-mcp/Dockerfile docker-compose.yml .env.example
git commit -m "feat(kb): optional cursor extra + KB_EXTRAS image build arg

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/SETUP.md` (§6 LLM backend)
- Modify: `README.md` (LLM mention in quickstart tail)

**Interfaces:** none (docs only).

- [ ] **Step 1: SETUP.md** — in §6, add a `cursor` row to the backend table:

```markdown
| Cursor (work subscription) | *(not a URL — set `KB_SYNTH_PROVIDER=cursor`)* | See "Cursor provider" below |
```

and append this subsection at the end of §6:

```markdown
### Cursor provider (office instances)

If your work LLM access is a Cursor subscription, the KB can run its LLM
features through the Cursor SDK instead of an OpenAI-wire endpoint. Each call
is a one-shot agent run in an empty scratch workspace — the agent never sees
your vault or any repository.

In `.env`:

    KB_SYNTH_PROVIDER=cursor
    CURSOR_API_KEY=crsr_...        # user API key from the Cursor dashboard
    KB_SYNTH_MODEL=composer-2.5    # or any id from your plan
    KB_EXTRAS=cursor               # bakes cursor-sdk into the image

Then rebuild and restart: `make up`. Smoke test (expects a cited answer or
"insufficient evidence"):

    KB_MCP_KEY=$(grep '^KB_MCP_KEY=' .env | cut -d= -f2)
    curl -s -X POST http://127.0.0.1:8077/mcp \
      -H "Authorization: Bearer $KB_MCP_KEY" \
      -H "Content-Type: application/json" \
      -H "Accept: application/json, text/event-stream" \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' -D - | grep -i mcp-session-id
    # then call tools/call ask with the returned session id, or just use the
    # kb tools from Claude Code / your MCP client.

Notes:

- Runs bill to your team's Cursor dashboard under your key's privacy rules.
  Keep `KB_EXTRACT_MAX_FACTS` and `KB_INGEST_MAX_SOURCES` modest at first —
  the nightly job makes one run per fact/source.
- Agent runs are slower than raw chat completions; `ask` latency is
  noticeably higher than with an OpenAI-wire backend.
- Team-admin API keys are not supported by the SDK; use a user or service
  account key.
```

- [ ] **Step 2: README** — replace the sentence "The LLM-backed features (`ask`, entity extraction, auto-ingest) are off until you set `KB_SYNTH_BASE_URL` — see the setup guide." with:

```markdown
The LLM-backed features (`ask`, entity extraction, auto-ingest) are off until
you configure a backend — any OpenAI-compatible endpoint via
`KB_SYNTH_BASE_URL`, or a Cursor subscription via `KB_SYNTH_PROVIDER=cursor`
— see the setup guide.
```

- [ ] **Step 3: Commit**

```bash
git add docs/SETUP.md README.md
git commit -m "docs(kb): Cursor provider setup (office instances)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Final verification

**Files:** none (verification only).

- [ ] **Step 1: Full test suite**

Run: `cd kb-mcp && .venv/bin/pytest -q`
Expected: all PASS (baseline 133 + ~12 new), 3 skipped

- [ ] **Step 2: Cursor-extra image builds and imports**

Run (repo root): `KB_EXTRAS=cursor docker compose build kb-mcp && docker run --rm --entrypoint python knowledge-base-kb-mcp -c "import cursor_sdk; from kb.cursor_llm import CursorAgentClient; print('cursor extra OK')"`
Expected: `cursor extra OK`

- [ ] **Step 3: Isolated e2e still green** (openai-path regression)

Run: `make verify`
Expected: `== 10 passed, 0 failed ==`

- [ ] **Step 4: Live-key smoke** — done by the user on the office machine per SETUP.md §6 "Cursor provider" (cannot run here: no Cursor key on this machine).
