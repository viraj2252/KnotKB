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
