from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Callable, TYPE_CHECKING

from kb.config import Config
from kb.dedup import DedupConfig, decide
from kb.embeddings import Embedder
from kb.markdown import write_fact, append_log, write_pending_marker
from kb.models import Fact
from kb.util import content_hash, make_id, validate_scope, is_scratch

if TYPE_CHECKING:
    from kb.db import VectorStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeBase:
    def __init__(self, store: VectorStore, embedder: Embedder, repo_path,
                 config: Config, clock: Callable[[], datetime] = _utcnow) -> None:
        self.store = store
        self.embedder = embedder
        self.repo_path = repo_path
        self.config = config
        self.clock = clock
        self._dedup = DedupConfig(config.dedup_merge, config.dedup_skip)

    def write(self, scope: str, content: str, tags=None, source=None) -> dict:
        validate_scope(scope)
        tags = list(tags or [])
        ts = self.clock()
        vector = self.embedder.embed([content])[0]

        neighbors = self.store.nearest(vector, scope, k=1, now=ts)
        best_sim = neighbors[0][1] if neighbors else None
        action = decide(best_sim, self._dedup)

        if action == "skipped":
            existing = neighbors[0][0]
            return {"id": existing.id, "path": existing.path, "action": "skipped"}

        ch = content_hash(content)
        fact = Fact(id=make_id(ts, ch), scope=scope, content=content, tags=tags,
                    source=source, ts=ts, content_hash=ch)

        scratch = is_scratch(scope)
        if scratch:
            fact.expires_at = ts + timedelta(seconds=self.config.scratch_ttl_seconds)
        else:
            write_fact(self.repo_path, fact)  # sets fact.path
            append_log(self.repo_path,
                       f"## [{ts.date().isoformat()}] write | {scope} | {content[:60]}")

        try:
            self.store.upsert(fact, vector)
        except Exception:
            if not scratch:
                write_pending_marker(self.repo_path, fact.id)
            return {"id": fact.id, "path": fact.path, "action": action}

        if action == "merged" and neighbors:
            self.store.mark_superseded(neighbors[0][0].id, fact.id)

        return {"id": fact.id, "path": fact.path, "action": action}

    def search(self, query: str, scope=None, tags=None, k: int = 8) -> list[dict]:
        if scope is None:
            scopes = ["global"]
        elif isinstance(scope, str):
            scopes = [scope]
        else:
            scopes = list(scope)
        for s in scopes:
            validate_scope(s)

        now = self.clock()
        qvec = self.embedder.embed([query])[0]
        hits = self.store.search(qvec, query, scopes=scopes, tags=tags, k=k, now=now)
        return [
            {
                "content": f.content,
                "score": round(score, 6),
                "scope": f.scope,
                "tags": f.tags,
                "source": f.source,
                "ts": f.ts.isoformat() if f.ts else None,
                "path": f.path,
            }
            for f, score in hits
        ]
