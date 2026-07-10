#!/bin/sh
# Seed the mounted vault so any empty directory is a valid KB.
# Idempotent: existing content is never touched. Also guards against the
# Docker footgun where a missing bind-mounted file gets created as a directory.
set -e

KB_ROOT="${KB_REPO_PATH:-/kb}"

mkdir -p "$KB_ROOT/memory/global" \
         "$KB_ROOT/wiki" \
         "$KB_ROOT/decisions" \
         "$KB_ROOT/sources" \
         "$KB_ROOT/review"

if [ -d "$KB_ROOT/log.md" ]; then
    echo "ERROR: $KB_ROOT/log.md is a directory (stale bind mount?). Remove it and restart." >&2
    exit 1
fi
[ -f "$KB_ROOT/log.md" ] || : > "$KB_ROOT/log.md"

exec "$@"
