#!/bin/sh
# Nightly KB consolidation runner. Invoked by launchd/systemd/cron.
# Resolves the repo from its own location, so schedulers only need this path.
set -eu

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# launchd/cron run with a minimal PATH that usually misses docker.
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"
export PATH

if ! docker compose --project-directory "$REPO_DIR" ps --status running kb-mcp 2>/dev/null | grep -q kb-mcp; then
    echo "kb-consolidate: kb-mcp container is not running (start with 'make up')" >&2
    exit 1
fi

exec docker compose --project-directory "$REPO_DIR" exec -T kb-mcp kb consolidate --apply
