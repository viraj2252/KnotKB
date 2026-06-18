from datetime import datetime, timezone
from kb.config import Config
from kb.reindex import reindex
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def test_reindex_rebuilds_from_markdown(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    # populate via KnowledgeBase (writes markdown)
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)
    kb.write("global", "alpha beta gamma")
    kb.write("project:p", "delta epsilon zeta")

    # fresh empty store; rebuild purely from markdown
    fresh = InMemoryVectorStore(emb)
    count = reindex(fresh, emb, tmp_path, cfg)
    assert count >= 2

    kb2 = KnowledgeBase(fresh, emb, tmp_path, cfg, clock=lambda: FIXED)
    res = kb2.search("alpha beta", scope=["global", "project:p"])
    assert any("alpha beta gamma" in r["content"] for r in res)

def test_reindex_clears_pending_markers(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    from kb.markdown import write_pending_marker, read_pending_markers
    write_pending_marker(tmp_path, "stale-id")
    reindex(InMemoryVectorStore(emb), emb, tmp_path, cfg)
    assert read_pending_markers(tmp_path) == []
