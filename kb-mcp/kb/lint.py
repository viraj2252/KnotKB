from pathlib import Path

from kb.config import Config
from kb.markdown import read_all_facts, read_pending_markers


def _normalize(tag: str) -> str:
    t = tag.lower().replace("-", "").replace("_", "")
    return t[:-1] if t.endswith("s") else t


def lint_report(repo_path: Path, config: Config) -> dict:
    facts = read_all_facts(repo_path, include_sources=False)
    tags = sorted({t for f in facts for t in f.tags})

    drift: list[tuple[str, str]] = []
    for i in range(len(tags)):
        for j in range(i + 1, len(tags)):
            a, b = tags[i], tags[j]
            if a != b and _normalize(a) == _normalize(b):
                drift.append((a, b))

    return {
        "tag_drift": drift,
        "pending_reindex": sorted(read_pending_markers(repo_path)),
    }
