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

def test_extract_subcommand(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from kb.models import Fact
    from kb.markdown import write_fact
    from tests.fakes import FakeEmbedder, InMemoryVectorStore, FakeLLM
    import datetime as _dt
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    store = InMemoryVectorStore(emb)
    write_fact(tmp_path, Fact(id="20260101000000-a", scope="project:flintt",
                              content="Flintt ships campaigns",
                              ts=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc), content_hash="a"))
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    monkeypatch.setattr(cli, "_llm", lambda c: FakeLLM())
    rc = cli.main(["extract"])
    assert rc == 0
    assert "facts_extracted=1" in capsys.readouterr().out

def test_review_list_and_accept_cli(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from kb.store import KnowledgeBase
    from kb.ingest import write_review_draft
    from tests.fakes import FakeEmbedder, InMemoryVectorStore
    import datetime as _dt
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder(); store = InMemoryVectorStore(emb)
    write_review_draft(tmp_path, "global", "held fact", ["t"], 40, "n.md",
                       _dt.datetime(2026, 6, 21, tzinfo=_dt.timezone.utc))
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    assert cli.main(["review"]) == 0
    assert "held fact" in capsys.readouterr().out
    assert cli.main(["review", "--accept"]) == 0
    assert "accepted=1" in capsys.readouterr().out


def test_ingest_subcommand(tmp_path, monkeypatch, capsys):
    from kb.config import Config
    from tests.fakes import FakeEmbedder, InMemoryVectorStore, FakeLLM
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder(); store = InMemoryVectorStore(emb)
    f = tmp_path / "note.md"; f.write_text("---\nkb_scope: global\n---\n\nbody")
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    monkeypatch.setattr(cli, "_llm", lambda c: FakeLLM(reply='[{"content":"X","confidence":99}]'))
    assert cli.main(["ingest", str(f)]) == 0
    assert "facts_written=1" in capsys.readouterr().out

def test_llm_returns_none_when_base_url_empty(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x", synth_base_url="")
    assert cli._llm(cfg) is None

def test_extract_exits_1_when_llm_disabled(tmp_path, monkeypatch, capsys):
    cfg = Config(repo_path=tmp_path, db_url="x", synth_base_url="")
    emb = FakeEmbedder(); store = InMemoryVectorStore(emb)
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    rc = cli.main(["extract"])
    assert rc == 1
    assert "KB_SYNTH_BASE_URL" in capsys.readouterr().out

def test_ingest_exits_1_when_llm_disabled(tmp_path, monkeypatch, capsys):
    cfg = Config(repo_path=tmp_path, db_url="x", synth_base_url="")
    emb = FakeEmbedder(); store = InMemoryVectorStore(emb)
    f = tmp_path / "note.md"; f.write_text("---\nkb_scope: global\n---\n\nbody")
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    rc = cli.main(["ingest", str(f)])
    assert rc == 1
    assert "KB_SYNTH_BASE_URL" in capsys.readouterr().out

def test_consolidate_runs_without_llm_when_base_url_empty(tmp_path, monkeypatch, capsys):
    import datetime as _dt
    cfg = Config(repo_path=tmp_path, db_url="x", synth_base_url="")
    emb = FakeEmbedder(); store = InMemoryVectorStore(emb)
    kb = KnowledgeBase(store, emb, tmp_path, cfg,
                       clock=lambda: _dt.datetime(2026, 6, 20, tzinfo=_dt.timezone.utc))
    kb.write("global", "a fact that would otherwise be sent for extraction")
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    cli.main(["consolidate"])
    assert "extracted=0" in capsys.readouterr().out

def test_consolidate_builds_llm_when_only_ingest_enabled(tmp_path, monkeypatch):
    from kb.config import Config
    from tests.fakes import FakeEmbedder, InMemoryVectorStore, FakeLLM
    cfg = Config(repo_path=tmp_path, db_url="x", extract_enabled=False, ingest_enabled=True)
    emb = FakeEmbedder(); store = InMemoryVectorStore(emb)
    called = {"n": 0}
    def fake_llm(c):
        called["n"] += 1
        return FakeLLM(reply="[]")
    monkeypatch.setattr(cli, "_load", lambda: (cfg, store, emb))
    monkeypatch.setattr(cli, "_llm", fake_llm)
    cli.main(["consolidate"])
    assert called["n"] == 1
