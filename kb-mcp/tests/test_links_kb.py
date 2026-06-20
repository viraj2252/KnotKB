from datetime import datetime, timezone
from kb.config import Config
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 20, tzinfo=timezone.utc)

def test_backlinks_from_written_facts(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)
    # a wiki page (file stem = slug) + a fact linking to it
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "ai-trends.md").write_text("# AI trends\nbody")
    kb.write("global", "reading about [[ai-trends]] today")
    back = kb.get_backlinks("ai-trends")
    assert any("reading about" in r["content"] for r in back)

def test_orphans_lists_unlinked(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)
    kb.write("global", "an unlinked standalone fact")
    assert any("unlinked standalone" in r["content"] for r in kb.orphans())

def test_orphans_excludes_superseded(tmp_path):
    from kb.models import Fact
    from kb.markdown import write_fact
    from datetime import datetime, timezone
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    kb = KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)
    dead = Fact(id="20260101000000-dead", scope="global", content="a superseded unlinked fact",
                ts=datetime(2026, 1, 1, tzinfo=timezone.utc), content_hash="d",
                superseded_by="somenewid")
    write_fact(tmp_path, dead)
    assert all("superseded unlinked" not in r["content"] for r in kb.orphans())
