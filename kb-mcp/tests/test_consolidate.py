from datetime import datetime, timezone, timedelta
from kb.config import Config
from kb.store import KnowledgeBase
from kb.consolidate import consolidate
from kb.reindex import reindex
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 20, tzinfo=timezone.utc)

def build(tmp_path, store):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(store, emb, tmp_path, cfg, clock=lambda: FIXED), cfg, emb

def test_reports_without_apply(tmp_path):
    from kb.models import Fact
    from kb.markdown import write_fact
    from datetime import datetime, timezone
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    # Inject two active near-dup facts directly (bypassing write's dedup merge)
    a = Fact(id="20260101000000-aaa", scope="global",
             content="alpha beta gamma delta epsilon zeta eta theta iota",
             ts=datetime(2026, 1, 1, tzinfo=timezone.utc), content_hash="aaa")
    b = Fact(id="20260102000000-bbb", scope="global",
             content="alpha beta gamma delta epsilon zeta eta theta iota kappa",
             ts=datetime(2026, 1, 2, tzinfo=timezone.utc), content_hash="bbb")
    write_fact(tmp_path, a)
    write_fact(tmp_path, b)
    reindex(store, emb, tmp_path, cfg)
    report = consolidate(store, emb, tmp_path, cfg, apply=False, now=FIXED)
    assert report["near_dups"]            # detected
    assert report["auto_merged"] == []    # nothing changed without apply

def test_auto_merge_supersedes_and_survives_reindex(tmp_path):
    from kb.models import Fact
    from kb.markdown import write_fact
    from datetime import datetime, timezone
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    content = "alpha beta gamma delta epsilon zeta eta theta iota"
    older = Fact(id="20260101000000-aaa", scope="global", content=content,
                 ts=datetime(2026, 1, 1, tzinfo=timezone.utc), content_hash="aaa")
    newer = Fact(id="20260102000000-bbb", scope="global", content=content,
                 ts=datetime(2026, 1, 2, tzinfo=timezone.utc), content_hash="bbb")
    write_fact(tmp_path, older)   # sets older.path
    write_fact(tmp_path, newer)   # sets newer.path
    reindex(store, emb, tmp_path, cfg)            # both active in the store
    report = consolidate(store, emb, tmp_path, cfg, apply=True, now=FIXED)
    assert report["auto_merged"]                  # consolidate merged the pair
    # superseded older is hidden, survives a fresh reindex
    fresh = InMemoryVectorStore(emb)
    reindex(fresh, emb, tmp_path, cfg)
    kb2 = KnowledgeBase(fresh, emb, tmp_path, cfg, clock=lambda: FIXED)
    paths = {r["path"] for r in kb2.search("alpha beta", scope=["global"], k=10)}
    assert older.path not in paths
    assert newer.path in paths

def test_staleness_report_only(tmp_path):
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x", stale_days=10)
    emb = FakeEmbedder()
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    kb = KnowledgeBase(store, emb, tmp_path, cfg, clock=lambda: old)
    kb.write("global", "an old standalone fact")
    report = consolidate(store, emb, tmp_path, cfg, apply=True, now=FIXED)
    assert any("old standalone" in s["content"] for s in report["stale"])
    # report-only: the stale fact is NOT superseded
    assert report["auto_merged"] == []

def test_consolidate_ignores_superseded(tmp_path):
    from kb.models import Fact
    from kb.markdown import write_fact
    from datetime import datetime, timezone
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    dead = Fact(id="20260101000000-dead", scope="global", content="alpha beta gamma delta",
                ts=datetime(2026, 1, 1, tzinfo=timezone.utc), content_hash="d",
                superseded_by="somenewid")
    write_fact(tmp_path, dead)
    reindex(store, emb, tmp_path, cfg)
    report = consolidate(store, emb, tmp_path, cfg, apply=True, now=FIXED)
    assert "20260101000000-dead" not in str(report)  # superseded fact never reported


def test_consolidate_runs_extract_phase(tmp_path):
    from kb.models import Fact
    from kb.markdown import write_fact
    from datetime import datetime, timezone
    from tests.fakes import FakeLLM
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    write_fact(tmp_path, Fact(id="20260101000000-a", scope="project:flintt",
                              content="Flintt ships campaigns", ts=datetime(2026,1,1,tzinfo=timezone.utc),
                              content_hash="a"))
    report = consolidate(store, emb, tmp_path, cfg, apply=True, now=FIXED, llm=FakeLLM())
    assert report["extracted"]["facts_extracted"] == 1
    assert (tmp_path / "entities" / "flintt.md").exists()

def test_consolidate_no_llm_skips_extract(tmp_path):
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x")
    report = consolidate(store, FakeEmbedder(), tmp_path, cfg, apply=True, now=FIXED)
    assert report["extracted"] == {"facts_extracted": 0, "entities_created": 0, "skipped": 0}


def test_consolidate_runs_ingest_phase_before_extract(tmp_path):
    from kb.models import Fact
    from datetime import datetime, timezone
    from tests.fakes import FakeLLM
    store = InMemoryVectorStore(FakeEmbedder())
    cfg = Config(repo_path=tmp_path, db_url="x", extract_enabled=False)  # isolate ingest
    emb = FakeEmbedder()
    src = tmp_path / "sources"; src.mkdir()
    (src / "n.md").write_text("---\nkb_scope: project:flintt\n---\n\nAnna owns testing")
    report = consolidate(store, emb, tmp_path, cfg, apply=True, now=FIXED,
                         llm=FakeLLM(reply='[{"content":"Anna owns testing","confidence":95}]'))
    assert report["ingested"]["sources_ingested"] == 1
    assert list((tmp_path / "memory" / "project" / "flintt").glob("*.md"))
