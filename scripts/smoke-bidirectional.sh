#!/usr/bin/env bash
set -euo pipefail

# Extract KB_MCP_KEY from .env
KB_KEY=$(grep '^KB_MCP_KEY=' "$(dirname "$0")/../.env" | cut -d= -f2)

echo "=== Bidirectional Smoke Test ==="
echo ""
echo "Step 1: Writing via Claude Code's kb tool..."
echo ""

# Write via Claude Code's MCP registration:
claude -p 'Use the kb tool memory_write to store: scope "global", content "bidirectional smoke test alpha", tags ["smoke"]. Then confirm the returned action.'

echo ""
echo "=== Write Complete ==="
echo ""
echo "Step 2: Reading back via Hermes..."
echo ""
echo "Now ask Hermes (via Open WebUI at http://127.0.0.1:3000 or the dashboard)"
echo "to execute: memory_search \"bidirectional smoke test\""
echo ""
echo "Expected: Hermes returns the fact Claude Code wrote above,"
echo "proving one shared knowledge base store with bidirectional access."
