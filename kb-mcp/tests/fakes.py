import hashlib
import math
from datetime import datetime

from kb.models import Fact
from kb.db import rrf_fuse


class FakeEmbedder:
    """Deterministic word-bag embedder for tests (no model download)."""
    dim = 512

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in t.lower().split():
                h = int(hashlib.sha1(tok.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


def _cos(u: list[float], v: list[float]) -> float:
    return sum(x * y for x, y in zip(u, v))


class InMemoryVectorStore:
    """Full VectorStore implementation backed by Python dicts (tests only).
    Keyed by fact.id to match the real PgVectorStore (id PRIMARY KEY)."""

    def __init__(self, embedder: FakeEmbedder) -> None:
        self._embedder = embedder
        self._rows: dict[str, tuple[Fact, list[float]]] = {}

    def upsert(self, fact: Fact, vector: list[float]) -> None:
        self._rows[fact.id] = (fact, vector)

    def _active(self, now: datetime | None = None):
        for fact, vec in self._rows.values():
            if fact.superseded_by:
                continue
            if now is not None and fact.expires_at is not None and fact.expires_at <= now:
                continue
            yield fact, vec

    def nearest(self, vector: list[float], scope: str, k: int = 1):
        scored = [(f, _cos(vector, vec)) for f, vec in self._active()
                  if f.scope == scope]
        scored.sort(key=lambda fv: -fv[1])
        return scored[:k]

    def search(self, query_vector, query_text, scopes, tags, k, now):
        cands = [(f, vec) for f, vec in self._active(now) if f.scope in scopes]
        if tags:
            tagset = set(tags)
            cands = [(f, vec) for f, vec in cands if tagset & set(f.tags)]
        by_id = {f.id: f for f, _ in cands}
        vector_ranked = [f.id for f, _ in sorted(
            cands, key=lambda fv: -_cos(query_vector, fv[1]))]
        qtokens = set(query_text.lower().split())
        fts_ranked = [f.id for f, _ in sorted(
            cands,
            key=lambda fv: -len(qtokens & set(fv[0].content.lower().split())))]
        fused = rrf_fuse([vector_ranked, fts_ranked])
        return [(by_id[i], score) for i, score in fused[:k] if i in by_id]

    def mark_superseded(self, old_id: str, new_id: str) -> None:
        if old_id in self._rows:
            fact, vec = self._rows[old_id]
            fact.superseded_by = new_id

    def delete_expired_scratch(self, now: datetime) -> int:
        expired = [i for i, (f, _) in self._rows.items()
                   if f.expires_at is not None and f.expires_at <= now]
        for i in expired:
            del self._rows[i]
        return len(expired)

    def clear(self) -> None:
        self._rows.clear()
