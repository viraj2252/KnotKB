from datetime import datetime, timezone
from kb.config import Config
from kb.lint import lint_report
from kb.store import KnowledgeBase
from kb.markdown import write_pending_marker
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def build(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED), cfg

def test_detects_tag_drift(tmp_path):
    kb, cfg = build(tmp_path)
    kb.write("global", "one", tags=["ai-trends"])
    kb.write("global", "two", tags=["ai-trend"])     # near-duplicate of ai-trends
    report = lint_report(tmp_path, cfg)
    pairs = {frozenset(p) for p in report["tag_drift"]}
    assert frozenset({"ai-trend", "ai-trends"}) in pairs

def test_no_false_positive_for_distinct_tags(tmp_path):
    kb, cfg = build(tmp_path)
    kb.write("global", "one", tags=["startup-idea"])
    kb.write("global", "two", tags=["life-lesson"])
    assert lint_report(tmp_path, cfg)["tag_drift"] == []

def test_reports_pending_reindex(tmp_path):
    _, cfg = build(tmp_path)
    write_pending_marker(tmp_path, "stuck-id")
    assert lint_report(tmp_path, cfg)["pending_reindex"] == ["stuck-id"]
