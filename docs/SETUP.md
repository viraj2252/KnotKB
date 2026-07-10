# KnotKB Setup Guide (macOS · WSL2 · Linux)

This is the canonical setup guide. The stack is fully self-contained in this
repo: `docker compose up` starts Postgres (pgvector) and the `kb-mcp` MCP
server on a private Docker network, bound to `127.0.0.1` only. The older docs
under `docs/reference/` that reference the `hermes-test` stack are historical;
Hermes integration is now an optional overlay described [below](#8-hermes-integration-optional).

## Contents

1. [Architecture in one minute](#1-architecture-in-one-minute)
2. [Prerequisites per platform](#2-prerequisites-per-platform)
3. [Configuration](#3-configuration)
4. [First boot](#4-first-boot)
5. [MCP registration](#5-mcp-registration)
6. [LLM backend (optional)](#6-llm-backend-optional)
7. [Nightly consolidation](#7-nightly-consolidation)
8. [Hermes integration (optional)](#8-hermes-integration-optional)
9. [Verification & troubleshooting](#9-verification--troubleshooting)
10. [Upgrades & rebuilds](#10-upgrades--rebuilds)

## 1. Architecture in one minute

Markdown files in a vault directory are the **source of truth** — facts under
`memory/`, curated pages under `wiki/`, plus `decisions/`, `sources/`,
`entities/`, `review/`, and `log.md`. The vault is bind-mounted into the
container at `/kb` from whatever `KB_HOST_PATH` points to.

Postgres+pgvector holds a **derived index** (vector + full-text, fused with
RRF, optionally reranked by a local cross-encoder). It is disposable: wipe it
and `kb reindex` rebuilds it from markdown. Embeddings run in-process
(fastembed ONNX on CPU) — no API keys needed for search/write.

The MCP server speaks **streamable HTTP** at `/mcp`, protected by a bearer
token (`KB_MCP_KEY`). `/health` is unauthenticated. Nothing listens beyond
`127.0.0.1` unless you change the compose file.

## 2. Prerequisites per platform

You need Docker with the compose v2 plugin (`docker compose`, not the legacy
`docker-compose`), `git`, `curl`, and `make`. Python 3.12 is only needed if
you develop on the package itself.

**Memory:** with the reranker enabled (default), kb-mcp uses ~1.5 GiB resident
and spikes higher while loading models at startup. Give the Docker VM
(Docker Desktop / Colima / WSL2) at least 4 GiB free headroom, or set
`KB_RERANK_ENABLED=false` in `.env` for a lean install — hybrid search still
works, results just skip the cross-encoder rerank.

### macOS

Docker Desktop or Colima both work.

```bash
brew install --cask docker    # or: brew install colima docker docker-compose && colima start
```

**Colima note:** Colima only shares `$HOME` into the VM by default. If your
Obsidian vault lives outside your home directory (or on an external volume),
add the path as a mount: `colima start --mount <vault-path>:w`.

### Linux

Install the Docker engine and compose plugin from Docker's apt/dnf repo
(distro packages are often stale), then let your user talk to the daemon:

```bash
sudo usermod -aG docker "$USER" && newgrp docker
```

Run `make init` (done automatically by `make up`) before the first boot so the
vault directory is created by your user, not root, when `KB_HOST_PATH` is
unset.

### Windows (WSL2)

Run everything **inside** a WSL2 distro (Ubuntu recommended). Two Docker
options:

- **Docker Desktop for Windows** with the WSL2 backend — enable your distro
  under *Settings → Resources → WSL Integration*.
- **Docker engine inside WSL2** — same install as Linux; requires systemd
  (see below) or manual `sudo service docker start`.

Keep the repo **and the vault on the Linux filesystem** (e.g. `~/knowledge-base`,
not `/mnt/c/...`). Bind mounts from `/mnt/c` are slow and file-watching is
unreliable. If your vault must stay on the Windows side (e.g. Obsidian runs on
Windows), accept the `/mnt/c/...` path for `KB_HOST_PATH` but expect slower
indexing.

For systemd (needed by the recommended scheduler), ensure `/etc/wsl.conf` has:

```ini
[boot]
systemd=true
```

then run `wsl --shutdown` from Windows and reopen the distro.

## 3. Configuration

```bash
cp .env.example .env
```

Then edit `.env`. Only one value is mandatory:

- **`KB_MCP_KEY`** — the bearer token; generate with `openssl rand -hex 32`.

The two you'll most likely touch:

- **`KB_HOST_PATH`** — host folder mounted as the vault. Point it at your
  Obsidian vault's `agent-kb/` folder to share knowledge with Obsidian (and get
  its sync/version history for durability). Leave it empty to use a local,
  gitignored `./kb-data` directory. Either way, an empty folder is fine — the
  container seeds the expected structure (`memory/global/`, `wiki/`, …) on
  start. See `example/` for the layout.
- **`KB_MCP_PORT`** — host port (default 8077). Change it if something else
  owns 8077.

Everything else in `.env.example` is documented inline and grouped: Postgres
credentials, dedup/rerank/search tuning, and the LLM-dependent groups covered
in [§6](#6-llm-backend-optional). Defaults are sensible; you can ignore them
on a first install.

## 4. First boot

```bash
make up
```

The first boot builds the image and, on first use, downloads embedding +
reranker models (~90 MB total) inside the container — the healthcheck allows
up to two minutes for this. Then:

```bash
make health     # -> healthy
make reindex    # index whatever markdown is already in the vault
make lint       # optional: tag-drift / index health report
make logs       # if anything looks off
```

If you started with an empty vault there is nothing to index yet — that's
fine; facts written via MCP are indexed immediately.

To pre-seed an empty vault with the example structure: `make seed-example`.

## 5. MCP registration

### Claude Code

```bash
KB_MCP_KEY=$(grep '^KB_MCP_KEY=' .env | cut -d= -f2)
claude mcp add kb --transport http --scope user http://127.0.0.1:8077/mcp \
  --header "Authorization: Bearer ${KB_MCP_KEY}"
claude mcp list    # expect: kb ... ✔ Connected
```

`--scope user` makes the tools available in every project. If you changed
`KB_MCP_PORT`, adjust the URL.

### Any other MCP client

Any client that speaks MCP streamable HTTP works: URL
`http://127.0.0.1:8077/mcp`, header `Authorization: Bearer <KB_MCP_KEY>`.

**Security note:** the server disables the MCP transport's DNS-rebinding
Host-header check so that containers on a shared Docker network can reach it
by service name (`kb-mcp:8077`). The bearer token is the access gate, and the
port is bound to `127.0.0.1` — do not expose it beyond localhost/private
Docker networks without adding real authentication in front.

## 6. LLM backend (optional)

Search, write, links, and the knowledge graph queries all work with **no LLM
at all**. Three features need one:

| Feature | What it does | Without an LLM |
| --- | --- | --- |
| `ask` (MCP tool) | Answers questions with citations | Returns a clean "not configured" error |
| `kb extract` / nightly extract | Builds typed entity pages from facts | Skipped |
| `kb ingest` / nightly ingest | Distills `sources/` into facts (confidence-gated) | `kb ingest` exits with a hint; nightly phase skipped |

Enable them by pointing `KB_SYNTH_BASE_URL` at **any OpenAI-compatible chat
endpoint** and restarting (`make up`):

| Backend | `KB_SYNTH_BASE_URL` | Notes |
| --- | --- | --- |
| claude-proxy (Hermes stack) | `http://claude-proxy:8000/v1` | Requires the [Hermes overlay](#8-hermes-integration-optional); set `KB_SYNTH_KEY` if the proxy checks one |
| OpenAI | `https://api.openai.com/v1` | `KB_SYNTH_KEY=sk-...`, `KB_SYNTH_MODEL=gpt-4o-mini` (or similar) |
| Ollama | `http://host.docker.internal:11434/v1` | `KB_SYNTH_MODEL=llama3.1` (or any pulled model); see Linux note |
| LiteLLM / any proxy | `http://<host>:<port>/v1` | Whatever the proxy fronts |
| Cursor (work subscription) | *(not a URL — set `KB_SYNTH_PROVIDER=cursor`)* | See "Cursor provider" below |

**Linux note:** `host.docker.internal` doesn't resolve by default on the
Linux Docker engine. Add this to the `kb-mcp` service in `docker-compose.yml`
(or an override file) when your backend runs on the host:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

`KB_SYNTH_MODEL` is the default model for all three features;
`KB_EXTRACT_MODEL` / `KB_INGEST_MODEL` override it per-feature. Leaving
`KB_SYNTH_BASE_URL` empty keeps everything cleanly disabled — nothing will
attempt an LLM call.

### Cursor provider (office instances)

If your work LLM access is a Cursor subscription, the KB can run its LLM
features through the Cursor SDK instead of an OpenAI-wire endpoint. Each call
is a one-shot agent run in an empty scratch workspace — the agent never sees
your vault or any repository.

In `.env`:

```dotenv
KB_SYNTH_PROVIDER=cursor
CURSOR_API_KEY=crsr_...        # user API key from the Cursor dashboard
KB_SYNTH_MODEL=composer-2.5    # or any id from your plan
KB_EXTRAS=cursor               # bakes cursor-sdk into the image
```

Then rebuild and restart: `make up`. Smoke test (expects a cited answer or
"insufficient evidence"):

```bash
KB_MCP_KEY=$(grep '^KB_MCP_KEY=' .env | cut -d= -f2)
curl -s -X POST http://127.0.0.1:8077/mcp \
  -H "Authorization: Bearer $KB_MCP_KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' -D - | grep -i mcp-session-id
# then call tools/call ask with the returned session id, or just use the
# kb tools from Claude Code / your MCP client.
```

Notes:

- Runs bill to your team's Cursor dashboard under your key's privacy rules.
  Keep `KB_EXTRACT_MAX_FACTS` and `KB_INGEST_MAX_SOURCES` modest at first —
  the nightly job makes one run per fact/source.
- Agent runs are slower than raw chat completions; `ask` latency is
  noticeably higher than with an OpenAI-wire backend.
- Team-admin API keys are not supported by the SDK; use a user or service
  account key.

## 7. Nightly consolidation

`kb consolidate --apply` runs the maintenance pipeline: auto-ingest opt-in
sources → entity extraction → auto-merge near-duplicates (≥ `KB_AUTOMERGE`) →
report stale facts, orphans, and tag drift. Reports land in `.kb/reports/`
inside the vault. Run it manually anytime:

```bash
docker compose exec kb-mcp kb consolidate          # report only
docker compose exec kb-mcp kb consolidate --apply  # apply safe merges + run LLM phases
```

To schedule it nightly at 03:30:

```bash
make schedule-install     # autodetects: launchd (macOS) / systemd user timer (Linux, WSL2) / cron (fallback)
make schedule-status
make schedule-uninstall
```

Per platform:

- **macOS (launchd):** plist installed to `~/Library/LaunchAgents/dev.kb.consolidate.plist`;
  logs at `~/Library/Logs/kb-consolidate.{log,err}`. The Mac must be awake at
  03:30 (launchd runs missed jobs on next wake only for some job types — if
  your Mac sleeps nightly, consider `sudo pmset repeat wake` or just run it manually).
- **Linux / WSL2 with systemd:** user timer with `Persistent=true` (missed
  runs fire on next boot). Logs: `journalctl --user -u kb-consolidate`. For
  runs while logged out: `loginctl enable-linger $USER`.
- **cron fallback (WSL2 without systemd, minimal distros):** crontab entry;
  log at `~/.local/state/kb/consolidate.log`. On WSL2, cron only runs while
  the distro is running (`sudo service cron start` if needed).

The job requires the stack to be up; it exits with an error (logged) if the
`kb-mcp` container isn't running.

## 8. Hermes integration (optional)

To share the KB with a Hermes agent running on the shared `hermes-net`
Docker network:

```bash
make up-hermes    # = docker compose -f docker-compose.yml -f docker-compose.hermes.yml up -d
```

This attaches `kb-mcp` to `hermes-net` (creating it if needed) in addition to
its private network; Postgres stays private. Hermes and claude-proxy then
reach the server at `http://kb-mcp:8077/mcp`.

> **Migrating from the hermes-test-managed stack:** if kb-mcp/kb-postgres are
> currently defined in `~/development/hermes-test/docker-compose.yml`, stop and
> remove those two services there **first** — otherwise you get a duplicate
> `kb-mcp` DNS alias on hermes-net and a clash on host port 8077. Reuse the
> same `KB_MCP_KEY` and `KB_HOST_PATH` values in this repo's `.env` and the
> existing registrations keep working. The pgvector index is disposable: after
> switching stacks, run `make reindex`.

Register in Hermes via `config.yaml` (the dashboard form can't set HTTP
headers):

```yaml
mcp_servers:
  kb:
    url: "http://kb-mcp:8077/mcp"
    headers:
      Authorization: "Bearer <KB_MCP_KEY>"
    timeout: 180
    enabled: true
```

Then run the bidirectional smoke test: `scripts/smoke-bidirectional.sh`.

## 9. Verification & troubleshooting

**End-to-end check** (safe alongside a live stack — isolated compose project,
port 8078, throwaway data, torn down afterwards):

```bash
make verify
```

It boots a fresh stack, checks health, rejects unauthenticated requests,
completes an MCP handshake, lists all 8 tools, does a `memory_write` →
`memory_search` round-trip, confirms `ask` degrades cleanly without an LLM,
and runs `kb reindex`/`kb consolidate` in the container.

Common issues:

| Symptom | Cause / fix |
| --- | --- |
| `RuntimeError: kb-mcp cannot start: missing required env var(s) ...` in logs | No `.env` (or empty values). `cp .env.example .env` and set them. |
| `port is already allocated` on 8077 | Another stack owns it (e.g. hermes-test). Set `KB_MCP_PORT=8078` in `.env`, re-run `make up`, update your `claude mcp` registration URL. |
| Container unhealthy for a few minutes after first boot | Model download + load. Wait for the healthcheck `start_period` (10 min); watch `make logs`. |
| Container repeatedly `Exited (137)` during startup | OOM-killed while loading the reranker model. Free memory in the Docker VM (or raise its limit), or set `KB_RERANK_ENABLED=false` in `.env`. |
| Models re-download on every recreate | Expected — they live in the container layer cache, not a volume. Rebuilds are the only trigger; add a volume for `/root/.cache` if it bothers you. |
| `401 unauthorized` from clients | Token mismatch. The header must be exactly `Authorization: Bearer <KB_MCP_KEY>` with the value from **this** repo's `.env`. |
| `421 Misdirected Request` | You re-enabled DNS-rebinding protection or front the server with a proxy that rewrites Host. See the security note in §5. |
| `kb-data`/vault files owned by root (Linux) | The dir was created by the Docker daemon. `sudo chown -R $USER kb-data`; `make up` runs `make init` to prevent this. |
| `log.md is a directory` error on start | Stale artifact of the old per-file bind mounts. Delete the `log.md` directory in the vault and restart. |
| Search returns nothing after restoring/moving a vault | Index is stale or empty: `make reindex`. |
| Postgres broken beyond repair | It's disposable: `docker compose down -v && make up && make reindex`. |

Package tests (development): `cd kb-mcp && python -m pytest` — integration
tests against a real Postgres are skipped unless `KB_TEST_DB_URL` is set.

## 10. Upgrades & rebuilds

```bash
git pull
make up          # rebuilds the image if kb-mcp/ changed
```

The markdown vault is the only thing that matters; protect it with whatever
already syncs it (Obsidian sync / Drive / git). The pgvector volume is never
worth backing up — after any schema change, host move, or doubt, run
`make reindex`. Config changes in `.env` take effect on `make up` (compose
recreates the container).
