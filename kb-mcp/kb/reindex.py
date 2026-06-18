from pathlib import Path

from kb.config import Config
from kb.embeddings import Embedder
from kb.markdown import read_all_facts, read_pending_markers, clear_pending_marker


def reindex(store, embedder: Embedder, repo_path: Path, config: Config) -> int:
    store.clear()
    facts = read_all_facts(repo_path, include_sources=config.index_sources)
    if facts:
        vectors = embedder.embed([f.content for f in facts])
        for fact, vec in zip(facts, vectors):
            store.upsert(fact, vec)
    for marker in read_pending_markers(repo_path):
        clear_pending_marker(repo_path, marker)
    return len(facts)
