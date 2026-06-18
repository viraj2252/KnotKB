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
