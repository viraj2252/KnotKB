> **Historical.** Deployment/scheduling details below assume the hermes-test
> stack; the standalone compose in this repo is now canonical — see
> [../SETUP.md](../SETUP.md). Env knobs and CLI behavior remain accurate.

# Spec C ops — scriptable + confidence-gated ingest

- `kb ingest <file> [--scope S] [--force]` — distill a source into atomic facts via claude-proxy.
  Facts with confidence >= KB_INGEST_CONFIDENCE (85) are written; lower ones go to `review/`.
- Nightly `kb consolidate --apply` auto-ingests sources that have a `kb_scope:` front-matter
  directive and no `kb_ingested:` flag (capped at KB_INGEST_MAX_SOURCES), before entity extraction.
- `kb review` lists held drafts; `kb review --accept [--source X]` promotes survivors into the KB
  (prune the bad ones in Obsidian first). Drafts live in `review/` and are NOT searched until accepted.
- Opt-in: add `kb_scope: project:<name>` (or `global`) to a source's front-matter. Idempotent via
  `kb_ingested: true`; re-ingest with `--force`. Set `KB_INGEST_ENABLED=false` to disable the nightly phase.
- Note: the interactive `kb-ingest` skill should also set `kb_ingested: true` on sources it processes
  so the nightly CLI doesn't re-ingest them.
