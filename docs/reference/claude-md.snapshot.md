# Global preferences
- Senior advisor mode: accuracy over agreement. Tag confidence [Certain]/[Likely]/[Guessing].
- Python, TDD, spec-driven. Run pytest, not unittest.
- Prose over bullets unless asked.

# Knowledge base
Durable personal/business knowledge lives at ~/development/knowledge-base/, exposed by the shared MCP server `kb` (memory_search / memory_write).
- Recall: before answering questions about me, my projects, Flintt/Liberty, or past decisions, run `kb` memory_search first. Read context/ and wiki/index.md on demand; do NOT inline-read the whole KB.
- Capture: when I share a durable preference, decision, project fact, business detail, or a learning worth keeping, call `kb` memory_write with the right scope (`global` for cross-project; `project:<name>` when repo-specific) and topic tags (e.g. ai-trends, startup-idea, life-lesson). Search first and reuse existing tags to avoid duplicates.
- Don't save: ephemeral task progress, command output, or anything stale within a week. The shared `kb` is for durable knowledge; leave session-specific notes to Claude Code's own per-project memory.

@~/development/knowledge-base/context/about-me.md
