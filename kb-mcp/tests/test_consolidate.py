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
    store = InMemoryVectorStore(FakeEmbedder())
    kb, cfg, emb = build(tmp_path, store)
    kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota")
    kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota kappa")  # near-dup
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
