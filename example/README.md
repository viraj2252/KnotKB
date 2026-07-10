# Example agent-kb vault

This mirrors the structure the `kb` server expects at `$KB_HOST_PATH`
(set in this repo's `.env`; defaults to a local `./kb-data`). Real knowledge
lives in your Obsidian vault, NOT here. An empty folder also works — the
container seeds this structure on start (`make seed-example` copies these
sample files in).

- `memory/global/`, `memory/project/<name>/` — atomic facts (one .md per fact, YAML frontmatter)
- `wiki/` — curated topic pages (human filenames = slugs, link with [[wiki-slug]])
- `decisions/` — dated, append-only
- `sources/` — raw transcripts (not indexed by default)
- `log.md`, `index.md`
