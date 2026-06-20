from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Callable, TYPE_CHECKING

from kb.config import Config
from kb.dedup import DedupConfig, decide
from kb.embeddings import Embedder
from kb.discovery import rank_experts
from kb.links import build_link_index, fact_slug
from kb.markdown import (write_fact, append_log, write_pending_marker,
                         set_superseded, read_all_facts)
from kb.models import Fact
from kb.util import content_hash, make_id, validate_scope, is_scratch

if TYPE_CHECKING:
    from kb.db import VectorStore
    from kb.rerank import Reranker


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeBase:
    def __init__(self, store: VectorStore, embedder: Embedder, repo_path,
                 config: Config, clock: Callable[[], datetime] = _utcnow,
                 reranker: "Reranker | None" = None) -> None:
        self.store = store
        self.embedder = embedder
        self.repo_path = repo_path
        self.config = config
        self.clock = clock
        self.reranker = reranker
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
            if action == "merged" and neighbors:
                set_superseded(self.repo_path, neighbors[0][0], fact.id)

        try:
            self.store.upsert(fact, vector)
        except Exception:
            if not scratch:
                write_pending_marker(self.repo_path, fact.id)
            return {"id": fact.id, "path": fact.path, "action": action}

        if action == "merged" and neighbors:
            self.store.mark_superseded(neighbors[0][0].id, fact.id)

        return {"id": fact.id, "path": fact.path, "action": action}

    def _result(self, fact: Fact) -> dict:
        return {
            "content": fact.content, "scope": fact.scope, "tags": fact.tags,
            "source": fact.source, "ts": fact.ts.isoformat() if fact.ts else None,
            "path": fact.path, "slug": fact.slug,
        }

    def _facts(self) -> list[Fact]:
        facts = read_all_facts(self.repo_path, include_sources=self.config.index_sources)
        return [f for f in facts if not f.superseded_by]

    def get_backlinks(self, slug: str) -> list[dict]:
        facts = self._facts()
        idx = build_link_index(facts)
        byid = {f.id: f for f in facts}
        return [self._result(byid[i]) for i in idx["backlinks"].get(slug, []) if i in byid]

    def get_links(self, slug: str) -> list[dict]:
        facts = self._facts()
        idx = build_link_index(facts)
        src = idx["by_slug"].get(slug)
        if src is None:
            return []
        out = []
        for dst in idx["forward"].get(src.id, []):
            target = idx["by_slug"].get(dst)
            out.append({"slug": dst, "resolved": self._result(target) if target else None})
        return out

    def orphans(self) -> list[dict]:
        facts = self._facts()
        idx = build_link_index(facts)
        byid = {f.id: f for f in facts}
        return [self._result(byid[i]) for i in idx["orphans"] if i in byid]

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
        if self.reranker is not None and self.config.rerank_enabled:
            cand = self.store.search(qvec, query, scopes=scopes, tags=tags,
                                     k=self.config.rerank_candidates, now=now)
            hits = self.reranker.rerank(query, cand)[:k]
        else:
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

    def find_experts(self, query, entity_type="person", k=5, scope=None) -> list[dict]:
        results = self.search(query, scope=scope, k=max(k * 3, 10))
        facts = self._facts()
        idx = build_link_index(facts)
        by_path = {f.path: f for f in facts if f.path}
        out = []
        for slug, ent in rank_experts(results, by_path, idx, entity_type, k):
            d = self._result(ent)
            d["slug"] = slug
            out.append(d)
        return out

    def get_entity(self, slug) -> dict:
        facts = self._facts()
        idx = build_link_index(facts)
        byid = {f.id: f for f in facts}
        ent = idx["by_slug"].get(slug)
        mention_ids = idx["backlinks"].get(slug, [])
        mentions = [self._result(byid[i]) for i in mention_ids if i in byid]
        related: dict[str, int] = {}
        for i in mention_ids:
            for dst in idx["forward"].get(i, []):
                if dst != slug and idx["by_slug"].get(dst) is not None and \
                        idx["by_slug"][dst].entity_type is not None:
                    related[dst] = related.get(dst, 0) + 1
        related_slugs = sorted(related, key=lambda s: (-related[s], s))
        return {
            "entity": (self._result(ent) | {"slug": slug}) if ent is not None else None,
            "mentions": mentions,
            "related": [self._result(idx["by_slug"][s]) | {"slug": s} for s in related_slugs],
        }

    def find_orphans(self) -> dict:
        facts = self._facts()
        idx = build_link_index(facts)
        byid = {f.id: f for f in facts}
        fact_orphans, entity_orphans = [], []
        for i in idx["orphans"]:
            f = byid.get(i)
            if f is None:
                continue
            (entity_orphans if f.entity_type is not None else fact_orphans).append(
                self._result(f) | {"slug": fact_slug(f)})
        # also flag low-connectivity entity pages (mentioned <= 1x)
        for f in facts:
            if f.entity_type is not None:
                slug = fact_slug(f)
                if len(idx["backlinks"].get(slug, [])) <= 1 and \
                        not any(e["slug"] == slug for e in entity_orphans):
                    entity_orphans.append(self._result(f) | {"slug": slug})
        return {"facts": fact_orphans, "entities": entity_orphans}
