from datetime import datetime, timezone
from kb import cli
from kb.config import Config
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def test_reindex_subcommand_uses_injected_kb(tmp_path, monkeypatch, capsys):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    store = InMemoryVectorStore(emb)
    KnowledgeBase(store, emb, tmp_path, cfg, clock=lambda: FIXED).write("global", "alpha beta")

    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    rc = cli.main(["reindex"])
    assert rc == 0
    assert "indexed" in capsys.readouterr().out.lower()

def test_consolidate_subcommand(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from kb.store import KnowledgeBase
    from tests.fakes import FakeEmbedder, InMemoryVectorStore
    import datetime as _dt
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    store = InMemoryVectorStore(emb)
    kb = KnowledgeBase(store, emb, tmp_path, cfg,
                       clock=lambda: _dt.datetime(2026, 6, 20, tzinfo=_dt.timezone.utc))
    kb.write("global", "a standalone orphan fact")
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    rc = cli.main(["consolidate"])
    assert "orphans=" in capsys.readouterr().out
    assert rc == 1  # orphan reported

def test_lint_subcommand(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from kb.store import KnowledgeBase
    from tests.fakes import FakeEmbedder, InMemoryVectorStore
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    store = InMemoryVectorStore(emb)
    kb = KnowledgeBase(store, emb, tmp_path, cfg, clock=lambda: __import__("datetime").datetime(2026,6,18,tzinfo=__import__("datetime").timezone.utc))
    kb.write("global", "a", tags=["ai-trends"]); kb.write("global", "b", tags=["ai-trend"])
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    rc = cli.main(["lint"])
    assert rc == 1
    assert "tag-drift" in capsys.readouterr().out
