from datetime import datetime, timezone
from kb.config import Config
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def build(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    return KnowledgeBase(InMemoryVectorStore(FakeEmbedder()), FakeEmbedder(),
                         tmp_path, cfg, clock=lambda: FIXED)

def test_default_scope_is_global_only(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha beta global fact")
    kb.write("project:p", "alpha beta project fact")
    results = kb.search("alpha beta")
    scopes = {r["scope"] for r in results}
    assert scopes == {"global"}

def test_explicit_scope_list_widens(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha beta global fact")
    kb.write("project:p", "alpha beta project fact")
    results = kb.search("alpha beta", scope=["global", "project:p"])
    assert {r["scope"] for r in results} == {"global", "project:p"}

def test_scratch_isolation_between_agents(tmp_path):
    kb = build(tmp_path)
    kb.write("agent:a:scratch", "alpha beta secret of a")
    # default search (global) must not see it
    assert kb.search("alpha beta") == []
    # agent b's scratch must not see a's
    assert kb.search("alpha beta", scope="agent:b:scratch") == []
    # a's own scratch sees it
    assert any("secret of a" in r["content"]
               for r in kb.search("alpha beta", scope="agent:a:scratch"))

def test_tag_filter(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha idea one", tags=["startup-idea"])
    kb.write("global", "alpha lesson one", tags=["life-lesson"])
    results = kb.search("alpha", tags=["startup-idea"])
    assert len(results) == 1
    assert results[0]["tags"] == ["startup-idea"]

def test_result_dict_shape(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha beta", tags=["x"], source="conv")
    r = kb.search("alpha beta")[0]
    assert set(r.keys()) == {"content", "score", "scope", "tags", "source", "ts", "path"}

def test_search_uses_reranker_when_present(tmp_path):
    from tests.fakes import FakeReranker
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg,
                       clock=lambda: FIXED, reranker=FakeReranker())
    kb.write("global", "alpha beta gamma delta")   # most overlap with query
    kb.write("global", "alpha unrelated")
    kb.write("global", "totally other words")
    results = kb.search("alpha beta gamma", k=3)
    assert results[0]["content"] == "alpha beta gamma delta"

def test_search_without_reranker_unchanged(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)
    kb.write("global", "alpha beta")
    assert kb.search("alpha beta")  # still returns results (RRF order)

def test_backlink_boost_promotes_linked_fact(tmp_path):
    from kb.models import Fact
    from kb.markdown import write_fact
    from kb.reindex import reindex
    from tests.fakes import FakeReranker
    from datetime import datetime, timezone
    ts = datetime(2026, 6, 21, tzinfo=timezone.utc)
    cfg = Config(repo_path=tmp_path, db_url="x", backlink_boost=5.0)
    emb = FakeEmbedder()
    store = InMemoryVectorStore(emb)
    # two equally-relevant facts; only `target` is linked to (by `linker`)
    target = Fact(id="20260101000000-t", scope="global", content="alpha beta gamma",
                  slug="target", ts=ts, content_hash="t")
    other = Fact(id="20260101000000-o", scope="global", content="alpha beta gamma",
                 slug="other", ts=ts, content_hash="o")
    linker = Fact(id="20260101000000-l", scope="global", content="see [[target]]",
                  ts=ts, content_hash="l")
    for f in (target, other, linker):
        write_fact(tmp_path, f)
    reindex(store, emb, tmp_path, cfg)
    kb = KnowledgeBase(store, emb, tmp_path, cfg, clock=lambda: ts, reranker=FakeReranker())
    res = kb.search("alpha beta gamma", k=3)
    paths = [r["path"] for r in res]
    assert paths.index(target.path) < paths.index(other.path)  # boost promotes the linked fact

def test_backlink_boost_zero_is_spec_a_order(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x", backlink_boost=0.0)
    emb = FakeEmbedder()
    from tests.fakes import FakeReranker
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg,
                       clock=lambda: FIXED, reranker=FakeReranker())
    kb.write("global", "alpha beta gamma delta")
    kb.write("global", "alpha beta")
    res = kb.search("alpha beta gamma", k=2)
    assert res[0]["content"] == "alpha beta gamma delta"  # pure rerank order, unchanged
