# KB: Cursor SDK as an LLM provider (office instance)

Date: 2026-07-10
Status: approved

## Problem

The LLM-backed features (`ask`, entity extract, auto-ingest) currently require
an OpenAI-compatible chat endpoint (`KB_SYNTH_BASE_URL`). At work, the user's
available LLM access is a Cursor subscription (user API key). The Cursor
Python SDK (`cursor-sdk`, https://cursor.com/docs/sdk/python) is **agentic
only** — it exposes one-shot/stateful agent runs, not a chat-completions
wire — so it cannot be pointed at via `KB_SYNTH_BASE_URL`.

Deployment decision (user): the work KB is a **separate office instance**
(own vault, own Postgres, own `.env`). One LLM provider per instance; no
per-scope routing.

## Decision

Add `cursor` as a first-class LLM provider behind the existing `LLMClient`
protocol (`complete(messages: list[dict], model: str) -> str`). All three
consumers (`ask`, extract, ingest) already receive an injected `LLMClient`,
so they need no changes.

Rejected alternatives: a sidecar OpenAI-compat shim service (a second service
to operate for one consumer); docs-only via a workplace LiteLLM gateway
(assumes infra that doesn't exist, doesn't use the Cursor key).

## Design

### Configuration (`kb/config.py`)

- `synth_provider: str = "openai"` from `KB_SYNTH_PROVIDER` (`openai` | `cursor`).
- `cursor_api_key: str = ""` from `CURSOR_API_KEY`.
- Model selection unchanged: `KB_SYNTH_MODEL` (office sets e.g. `composer-2.5`),
  with `KB_EXTRACT_MODEL` / `KB_INGEST_MODEL` overrides still applying.
- `synth_configured(config)` becomes provider-aware:
  - `openai` → `bool(synth_base_url)` (today's behavior, unchanged default)
  - `cursor` → `bool(cursor_api_key)`

### Adapter (`kb/cursor_llm.py`, new)

`CursorAgentClient` implements the `LLMClient` protocol:

- `complete(messages, model)` flattens the system + user messages into one
  self-contained prompt, prefixed with a hard instruction to answer with text
  only and use no tools.
- Executes a one-shot `Agent.prompt(...)` (sync) with a **local runtime whose
  cwd is an empty throwaway workspace directory** (created once per client via
  `tempfile.mkdtemp`) — the agent never sees the vault, the repo, or any real
  files. Returns the run's final text.
- The `cursor-sdk` import is lazy (inside the class). If the package is
  missing, raise a clear error naming the fix (install the `cursor` extra /
  rebuild the image with `KB_EXTRAS=cursor`).
- Run errors propagate like `OpenAIWireClient` errors today: `synthesize()`
  catches into `{"error": ...}`; extract/ingest count per-item failures as
  skipped.

### Wiring (`kb/synth.py`, `kb/cli.py`, `kb/server.py`)

- New factory in `kb/synth.py`:
  `build_llm(config) -> LLMClient | None` — returns `None` when
  `not synth_configured(config)`, `CursorAgentClient` when provider is
  `cursor`, else `OpenAIWireClient(synth_base_url, synth_key)`.
- `cli._llm(cfg)` delegates to `build_llm`. The disabled hint becomes
  provider-aware: "set KB_SYNTH_BASE_URL, or KB_SYNTH_PROVIDER=cursor +
  CURSOR_API_KEY".
- `server.py` `ask` uses `build_llm(config)` instead of constructing
  `OpenAIWireClient` inline; its not-configured error message gets the same
  provider-aware wording.

### Packaging & deployment

- `pyproject.toml`: `[project.optional-dependencies] cursor = ["cursor-sdk"]`
  (pin a floor version at implementation time).
- `kb-mcp/Dockerfile`: build arg `KB_EXTRAS` (default empty); installs
  `.[<extras>]` when non-empty. The pip-bundled `cursor-sdk-bridge` binary is
  glibc-compatible with `python:3.12-slim`.
- `docker-compose.yml`: pass `KB_EXTRAS` as a build arg and `CURSOR_API_KEY` +
  `KB_SYNTH_PROVIDER` into the container environment (interpolated with empty
  defaults, same pattern as `KB_SYNTH_BASE_URL`).
- `.env.example`: provider block —
  `KB_SYNTH_PROVIDER=openai`, `CURSOR_API_KEY=`, `KB_EXTRAS=` with comments
  showing the office recipe.

Office-instance recipe (three lines + rebuild):

```
KB_SYNTH_PROVIDER=cursor
CURSOR_API_KEY=crsr_...
KB_SYNTH_MODEL=composer-2.5
KB_EXTRAS=cursor
```

### Docs

`docs/SETUP.md` LLM-backend section gains a "Cursor (work/office)"
subsection: the recipe above, rebuild command, a smoke test (`ask` via curl),
and notes that runs bill to the team's Cursor dashboard under the key's
privacy rules — keep `KB_EXTRACT_MAX_FACTS` / `KB_INGEST_MAX_SOURCES` modest
initially. README's tool list mentions Cursor as a supported provider.

### Testing (no live key required)

- Config: provider default `openai`; `KB_SYNTH_PROVIDER` / `CURSOR_API_KEY`
  parsed; provider-aware `synth_configured` cases.
- Adapter: inject a fake `cursor` SDK module (sys.modules) and assert message
  flattening, the no-tools preamble, model + api-key passthrough, and text
  extraction.
- `build_llm`: four states — openai → `OpenAIWireClient`; cursor →
  `CursorAgentClient`; unconfigured → `None`; cursor selected but package
  missing → actionable error.
- CLI: provider-aware hint text; existing LLM-disabled tests keep passing.
- Real-key smoke test happens on the office machine (`ask` round-trip).

## Non-goals

- Per-scope / per-request provider routing (one provider per instance).
- Cursor cloud runtime, conversation state, streaming, or tool use — each
  `complete()` is a stateless one-shot text answer.
- Team-admin API keys (unsupported by the SDK).

## Open implementation details (resolve during planning/implementation)

- Exact Python import path and current version floor of `cursor-sdk`
  (docs show `Agent`, `AgentOptions`, `LocalAgentOptions`, `Agent.prompt`;
  verify the module name on install).
- Whether `Agent.prompt` accepts `api_key` via options or only the
  `CURSOR_API_KEY` env var (adapter supports both by setting the env var for
  the process if needed).
