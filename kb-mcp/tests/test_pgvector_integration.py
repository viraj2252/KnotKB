import os
from datetime import datetime, timezone, timedelta
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("KB_TEST_DB_URL"),
    reason="set KB_TEST_DB_URL to run pgvector integration tests",
)

from kb.db import PgVectorStore, connect
from kb.models import Fact
from kb.util import content_hash, make_id
from tests.fakes import FakeEmbedder

EMB = FakeEmbedder()

def fact(content, scope="global", tags=()):
    ts = datetime.now(timezone.utc)
    ch = content_hash(content + scope + str(ts))
    return Fact(id=make_id(ts, ch), scope=scope, content=content, tags=list(tags),
                source=None, ts=ts, content_hash=ch)

@pytest.fixture
def store():
    conn = connect(os.environ["KB_TEST_DB_URL"])
    s = PgVectorStore(conn, dim=EMB.dim)
    s.ensure_schema()
    s.clear()
    yield s
    s.clear()
    conn.close()

def test_upsert_and_search_roundtrip(store):
    f = fact("alpha beta gamma", tags=["ai-trends"])
    store.upsert(f, EMB.embed([f.content])[0])
    now = datetime.now(timezone.utc)
    res = store.search(EMB.embed(["alpha beta"])[0], "alpha beta",
                       scopes=["global"], tags=None, k=5, now=now)
    assert any(rf.id == f.id for rf, _ in res)

def test_supersede_hides_row(store):
    f = fact("alpha beta")
    store.upsert(f, EMB.embed([f.content])[0])
    store.mark_superseded(f.id, "new")
    now = datetime.now(timezone.utc)
    res = store.search(EMB.embed(["alpha beta"])[0], "alpha beta",
                       scopes=["global"], tags=None, k=5, now=now)
    assert all(rf.id != f.id for rf, _ in res)

def test_expired_scratch_excluded_and_swept(store):
    f = fact("alpha beta tmp", scope="agent:x:scratch")
    f.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    store.upsert(f, EMB.embed([f.content])[0])
    now = datetime.now(timezone.utc)
    res = store.search(EMB.embed(["alpha beta"])[0], "alpha beta",
                       scopes=["agent:x:scratch"], tags=None, k=5, now=now)
    assert res == []
    assert store.delete_expired_scratch(now) >= 1
