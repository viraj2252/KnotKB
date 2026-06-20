# Registering kb-mcp

The kb-mcp server can be registered with both Claude Code and Hermes, allowing bidirectional knowledge sharing across clients.

## Prerequisites

1. The kb-mcp + kb-postgres services run as part of the **hermes-test** stack
   (`cd ~/development/hermes-test && make up`). They share `hermes-net` with Hermes.
2. `KB_MCP_KEY` lives in `~/development/hermes-test/.env` (same value as
   `knowledge-base/.env`). Extract it with:
   ```bash
   grep '^KB_MCP_KEY=' ~/development/hermes-test/.env | cut -d= -f2
   ```

## Claude Code (host)

Register kb-mcp as an MCP server on your local host:

```bash
KB_MCP_KEY=$(grep '^KB_MCP_KEY=' ~/development/hermes-test/.env | cut -d= -f2)
claude mcp add kb --transport http --scope user http://127.0.0.1:8077/mcp \
  --header "Authorization: Bearer ${KB_MCP_KEY}"
```
(`--scope user` makes the `kb` tools available in every project, not just one.)

Verify the registration:
```bash
claude mcp list      # expect: kb ... ✔ Connected
```

## Hermes (container)

**The dashboard "Add MCP Server" form cannot set HTTP headers** — it only exposes
an *Environment* field (which maps to stdio `env`, not HTTP headers). Since kb-mcp
requires `Authorization: Bearer`, register it in `config.yaml` instead, which
supports a `headers` block (per `tools/mcp_tool.py`):

```bash
cd ~/development/hermes-test
make config-pull                       # → ./hermes-data/config.yaml
# add this top-level block (KB_MCP_KEY from .env):
#
# mcp_servers:
#   kb:
#     url: "http://kb-mcp:8077/mcp"    # service name over hermes-net
#     headers:
#       Authorization: "Bearer <KB_MCP_KEY>"
#     timeout: 180
#     enabled: true
#
make config-push                       # copies into the volume + restarts hermes
```

Hermes connects to the server on session start and registers its `memory_search` /
`memory_write` tools. Note: kb-mcp disables the MCP transport's DNS-rebinding host
check (`TransportSecuritySettings(enable_dns_rebinding_protection=False)` in
`kb/server.py`) — required because Hermes reaches it by service name (`kb-mcp:8077`),
which the default localhost-only host allowlist would reject with `421 Misdirected
Request`. The bearer token remains the access gate.

## Verification

After both registrations are complete, run the bidirectional smoke test:

```bash
~/development/knowledge-base/scripts/smoke-bidirectional.sh
```

This writes a test fact via Claude Code's kb tool, then prompts you to read it back via Hermes, proving shared access to the same knowledge base store.
