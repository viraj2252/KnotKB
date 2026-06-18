from datetime import datetime, timezone, timedelta
from tests.fakes import FakeEmbedder, InMemoryVectorStore
from kb.models import Fact
from kb.util import content_hash, make_id

EMB = FakeEmbedder()

def fact(content, scope="global", tags=(), expires_at=None, superseded_by=None):
    ts = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)
    ch = content_hash(content + scope)
    return Fact(id=make_id(ts, ch), scope=scope, content=content, tags=list(tags),
                source=None, ts=ts, content_hash=ch, expires_at=expires_at,
                superseded_by=superseded_by)

def test_fake_embedder_similarity():
    [a, b, c] = EMB.embed(["alpha beta gamma", "alpha beta gamma delta", "zeta eta theta"])
    def cos(u, v): return sum(x*y for x, y in zip(u, v))
    assert cos(a, a) > 0.999
    assert cos(a, b) > cos(a, c)   # near-dup more similar than unrelated

def test_inmemory_nearest_same_scope_only():
    s = InMemoryVectorStore(EMB)
    f1 = fact("alpha beta")
    f2 = fact("alpha beta", scope="project:p")
    s.upsert(f1, EMB.embed([f1.content])[0])
    s.upsert(f2, EMB.embed([f2.content])[0])
    res = s.nearest(EMB.embed(["alpha beta"])[0], scope="global", k=5)
    assert [f.id for f, _ in res] == [f1.id]

def test_inmemory_search_excludes_superseded_and_expired():
    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    s = InMemoryVectorStore(EMB)
    live = fact("alpha beta")
    dead = fact("alpha beta old", superseded_by="someid")
    expired = fact("alpha beta tmp", scope="agent:x:scratch",
                   expires_at=now - timedelta(hours=1))
    for f in (live, dead, expired):
        s.upsert(f, EMB.embed([f.content])[0])
    res = s.search(EMB.embed(["alpha beta"])[0], "alpha beta",
                   scopes=["global", "agent:x:scratch"], tags=None, k=10, now=now)
    ids = [f.id for f, _ in res]
    assert live.id in ids
    assert dead.id not in ids
    assert expired.id not in ids

def test_inmemory_tag_filter():
    s = InMemoryVectorStore(EMB)
    a = fact("alpha one", tags=["startup-idea"])
    b = fact("alpha two", tags=["life-lesson"])
    s.upsert(a, EMB.embed([a.content])[0]); s.upsert(b, EMB.embed([b.content])[0])
    now = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    res = s.search(EMB.embed(["alpha"])[0], "alpha", scopes=["global"],
                   tags=["startup-idea"], k=10, now=now)
    assert [f.id for f, _ in res] == [a.id]
