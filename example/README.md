# Example agent-kb vault

This mirrors the structure the `kb` server expects at `$KB_HOST_PATH`
(set in hermes-test/.env). Real knowledge lives in your Obsidian vault, NOT here.

- `memory/global/`, `memory/project/<name>/` — atomic facts (one .md per fact, YAML frontmatter)
- `wiki/` — curated topic pages (human filenames = slugs, link with [[wiki-slug]])
- `decisions/` — dated, append-only
- `sources/` — raw transcripts (not indexed by default)
- `log.md`, `index.md`
