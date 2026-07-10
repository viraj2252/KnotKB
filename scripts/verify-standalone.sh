#!/bin/sh
# End-to-end verification of the standalone stack on an ISOLATED compose
# project (kb-verify), alternate port, and throwaway data dir — a live stack
# on the default port/project is never touched.
#
#   scripts/verify-standalone.sh [--keep]
#
# Requires: docker compose, curl, python3 (for JSON parsing). Reads KB_MCP_KEY
# from .env.
set -eu

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

PORT="${VERIFY_PORT:-8078}"
PROJECT=kb-verify
DATA_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kb-verify-data.XXXXXX")"
KEEP="${1:-}"

KB_MCP_KEY="$(sed -n 's/^KB_MCP_KEY=//p' .env | head -1)"
[ -n "$KB_MCP_KEY" ] || { echo "FAIL: KB_MCP_KEY not set in .env"; exit 1; }

URL="http://127.0.0.1:$PORT/mcp"
HDR_ACCEPT="Accept: application/json, text/event-stream"
HDR_AUTH="Authorization: Bearer $KB_MCP_KEY"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); echo "  ok: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL: $1"; }

# Rerank off for the verify run: loading the reranker ONNX weights spikes
# memory and OOMs when a production stack already runs on the same Docker VM.
# The rerank path is covered by unit tests; this script verifies wiring.
OVERRIDE="$(mktemp "${TMPDIR:-/tmp}/kb-verify-override.XXXXXX.yml")"
cat > "$OVERRIDE" <<'EOF'
services:
  kb-mcp:
    environment:
      KB_RERANK_ENABLED: "false"
EOF
COMPOSE="docker compose -p $PROJECT -f docker-compose.yml -f $OVERRIDE"

cleanup() {
    if [ "$KEEP" != "--keep" ]; then
        echo "-- teardown"
        KB_MCP_PORT="$PORT" KB_HOST_PATH="$DATA_DIR" \
            $COMPOSE down -v >/dev/null 2>&1 || true
        rm -rf "$DATA_DIR"
    else
        echo "-- kept: project=$PROJECT data=$DATA_DIR"
    fi
    rm -f "$OVERRIDE"
}
trap cleanup EXIT

echo "-- boot (project=$PROJECT port=$PORT data=$DATA_DIR)"
KB_MCP_PORT="$PORT" KB_HOST_PATH="$DATA_DIR" KB_SYNTH_BASE_URL="" \
    $COMPOSE up -d --build

echo "-- wait for health (model download can take a few minutes on first build)"
i=0
until curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; do
    i=$((i+1))
    if [ "$i" -gt 120 ]; then   # 10 min: cold HF model download can be slow
        bad "health endpoint never came up; last container logs:"
        docker logs "$(docker compose -p "$PROJECT" ps -q kb-mcp)" 2>&1 | tail -20
        exit 1
    fi
    sleep 5
done
ok "health endpoint"

[ -d "$DATA_DIR/memory/global" ] && [ -f "$DATA_DIR/log.md" ] \
    && ok "entrypoint seeded vault dirs" \
    || bad "vault dirs not seeded in $DATA_DIR"

CODE="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL" -H "$HDR_ACCEPT" \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"x","version":"0"}}}')"
[ "$CODE" = 401 ] && ok "unauthenticated request rejected (401)" \
    || bad "expected 401 without bearer, got $CODE"

HDRS="$(mktemp)"
INIT="$(curl -fsS -D "$HDRS" -X POST "$URL" -H "$HDR_ACCEPT" -H "$HDR_AUTH" \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"verify","version":"0"}}}')"
SESSION="$(tr -d '\r' < "$HDRS" | sed -n 's/^[Mm]cp-[Ss]ession-[Ii]d: //p' | head -1)"
rm -f "$HDRS"
[ -n "$SESSION" ] && echo "$INIT" | grep -q '"serverInfo"' \
    && ok "MCP initialize (session $SESSION)" \
    || { bad "initialize failed: $INIT"; exit 1; }

rpc() { # rpc <json-body>
    curl -fsS -X POST "$URL" -H "$HDR_ACCEPT" -H "$HDR_AUTH" \
        -H "Mcp-Session-Id: $SESSION" -H 'Content-Type: application/json' -d "$1"
}

rpc '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null || true

TOOLS="$(rpc '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')"
MISSING=""
for t in memory_write memory_search get_backlinks get_links find_experts get_entity find_orphans ask; do
    echo "$TOOLS" | grep -q "\"$t\"" || MISSING="$MISSING $t"
done
[ -z "$MISSING" ] && ok "tools/list exposes all 8 tools" \
    || bad "missing tools:$MISSING"

MARKER="verify-marker-$(date +%s)-$$"
WRITE="$(rpc "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"memory_write\",\"arguments\":{\"scope\":\"global\",\"content\":\"standalone verification fact $MARKER\"}}}")"
# the tool result is JSON-in-JSON, so "action" arrives escaped as \"action\"
echo "$WRITE" | grep -q 'action' && ! echo "$WRITE" | grep -q '"isError":true' \
    && ok "memory_write round-trip" \
    || bad "memory_write failed: $WRITE"

SEARCH="$(rpc "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"memory_search\",\"arguments\":{\"query\":\"standalone verification fact $MARKER\"}}}")"
echo "$SEARCH" | grep -q "$MARKER" && ok "memory_search finds the written fact" \
    || bad "memory_search missed the marker: $SEARCH"

ASK="$(rpc '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"ask","arguments":{"question":"anything"}}}')"
echo "$ASK" | grep -q 'LLM synthesis not configured' \
    && ok "ask degrades cleanly with LLM off" \
    || bad "ask did not return the not-configured error: $ASK"

echo "-- CLI inside the container"
KB_MCP_PORT="$PORT" KB_HOST_PATH="$DATA_DIR" \
    docker compose -p "$PROJECT" exec -T kb-mcp kb reindex \
    && ok "kb reindex" || bad "kb reindex failed"
# consolidate exits 1 when it has findings to report (our lone fact is an orphan)
RC=0
KB_MCP_PORT="$PORT" KB_HOST_PATH="$DATA_DIR" \
    docker compose -p "$PROJECT" exec -T kb-mcp kb consolidate >/dev/null 2>&1 || RC=$?
[ "$RC" = 0 ] || [ "$RC" = 1 ] && ok "kb consolidate (rc=$RC)" \
    || bad "kb consolidate crashed (rc=$RC)"

echo
echo "== $PASS passed, $FAIL failed =="
[ "$FAIL" = 0 ]
