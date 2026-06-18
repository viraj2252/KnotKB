from datetime import datetime
from typing import Protocol

import psycopg
from pgvector.psycopg import register_vector, Vector

from kb.models import Fact


def rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal-rank fusion. Deterministic; ties broken by id ascending."""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


class VectorStore(Protocol):
    def upsert(self, fact: Fact, vector: list[float]) -> None: ...
    def nearest(self, vector: list[float], scope: str, k: int = 1, now: datetime | None = None) -> list[tuple[Fact, float]]: ...
    def search(self, query_vector: list[float], query_text: str, scopes: list[str],
               tags: list[str] | None, k: int, now: datetime) -> list[tuple[Fact, float]]: ...
    def mark_superseded(self, old_id: str, new_id: str) -> None: ...
    def delete_expired_scratch(self, now: datetime) -> int: ...
    def clear(self) -> None: ...


def connect(db_url: str) -> "psycopg.Connection":
    conn = psycopg.connect(db_url, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def _row_to_fact(row) -> Fact:
    (fid, scope, content, tags, source, ts, chash, superseded_by, path, expires_at) = row
    return Fact(id=fid, scope=scope, content=content, tags=list(tags or []),
                source=source, ts=ts, content_hash=chash, superseded_by=superseded_by,
                path=path, expires_at=expires_at)


_COLS = ("id, scope, content, tags, source, ts, content_hash, "
         "superseded_by, path, expires_at")


class PgVectorStore:
    def __init__(self, conn: "psycopg.Connection", dim: int) -> None:
        self.conn = conn
        self.dim = dim

    def ensure_schema(self) -> None:
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS facts (
                id text PRIMARY KEY,
                scope text NOT NULL,
                content text NOT NULL,
                tags text[] NOT NULL DEFAULT '{{}}',
                source text,
                ts timestamptz NOT NULL,
                content_hash text NOT NULL,
                superseded_by text,
                path text,
                expires_at timestamptz,
                embedding vector({self.dim}),
                fts tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS facts_fts_idx ON facts USING gin (fts)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS facts_scope_idx ON facts (scope)")

    def upsert(self, fact: Fact, vector: list[float]) -> None:
        self.conn.execute(
            f"""INSERT INTO facts ({_COLS}, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                  content=EXCLUDED.content, tags=EXCLUDED.tags, source=EXCLUDED.source,
                  ts=EXCLUDED.ts, content_hash=EXCLUDED.content_hash,
                  superseded_by=EXCLUDED.superseded_by, path=EXCLUDED.path,
                  expires_at=EXCLUDED.expires_at, embedding=EXCLUDED.embedding""",
            (fact.id, fact.scope, fact.content, fact.tags, fact.source, fact.ts,
             fact.content_hash, fact.superseded_by, fact.path, fact.expires_at, Vector(vector)),
        )

    def _active_clause(self) -> str:
        return "superseded_by IS NULL AND (expires_at IS NULL OR expires_at > %(now)s)"

    def nearest(self, vector, scope, k=1, now=None):
        params = {"v": Vector(vector), "scope": scope, "k": k}
        expiry = ""
        if now is not None:
            expiry = "AND (expires_at IS NULL OR expires_at > %(now)s)"
            params["now"] = now
        rows = self.conn.execute(
            f"""SELECT {_COLS}, 1 - (embedding <=> %(v)s) AS sim FROM facts
                WHERE scope = %(scope)s AND superseded_by IS NULL {expiry}
                ORDER BY embedding <=> %(v)s LIMIT %(k)s""",
            params,
        ).fetchall()
        return [(_row_to_fact(r[:-1]), float(r[-1])) for r in rows]

    def search(self, query_vector, query_text, scopes, tags, k, now):
        params = {"v": Vector(query_vector), "q": query_text, "scopes": scopes,
                  "now": now, "lim": max(k * 4, 20)}
        tag_clause = ""
        if tags:
            tag_clause = "AND tags && %(tags)s"
            params["tags"] = tags
        base = f"FROM facts WHERE scope = ANY(%(scopes)s) AND {self._active_clause()} {tag_clause}"
        vec_ids = [r[0] for r in self.conn.execute(
            f"SELECT id {base} ORDER BY embedding <=> %(v)s LIMIT %(lim)s", params).fetchall()]
        fts_ids = [r[0] for r in self.conn.execute(
            f"SELECT id {base} AND fts @@ plainto_tsquery('english', %(q)s) "
            f"ORDER BY ts_rank(fts, plainto_tsquery('english', %(q)s)) DESC LIMIT %(lim)s",
            params).fetchall()]
        fused = rrf_fuse([vec_ids, fts_ids])[:k]
        if not fused:
            return []
        rows = self.conn.execute(
            f"SELECT {_COLS} FROM facts WHERE id = ANY(%s)",
            ([fid for fid, _ in fused],)).fetchall()
        facts = {f.id: f for f in (_row_to_fact(r) for r in rows)}
        return [(facts[fid], score) for fid, score in fused if fid in facts]

    def mark_superseded(self, old_id, new_id):
        self.conn.execute("UPDATE facts SET superseded_by=%s WHERE id=%s", (new_id, old_id))

    def delete_expired_scratch(self, now):
        cur = self.conn.execute(
            "DELETE FROM facts WHERE expires_at IS NOT NULL AND expires_at <= %s", (now,))
        return cur.rowcount

    def clear(self):
        self.conn.execute("TRUNCATE facts")
