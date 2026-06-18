from datetime import datetime, timezone
from pathlib import Path
import pytest

from kb.config import Config
from kb.store import KnowledgeBase
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)

def build(tmp_path) -> KnowledgeBase:
    cfg = Config(repo_path=tmp_path, db_url="x")
    return KnowledgeBase(InMemoryVectorStore(FakeEmbedder()), FakeEmbedder(),
                         tmp_path, cfg, clock=lambda: FIXED)

def test_create_writes_markdown_and_indexes(tmp_path):
    kb = build(tmp_path)
    r = kb.write("global", "alpha beta gamma", tags=["ai-trends"], source="conv")
    assert r["action"] == "created"
    assert Path(r["path"]).exists()
    assert "memory/global" in r["path"]
    assert "## [2026-06-18] write | global" in (tmp_path / "log.md").read_text()

def test_exact_duplicate_skipped(tmp_path):
    kb = build(tmp_path)
    kb.write("global", "alpha beta gamma")
    r = kb.write("global", "alpha beta gamma")
    assert r["action"] == "skipped"

def test_near_duplicate_merged_and_supersedes(tmp_path):
    kb = build(tmp_path)
    first = kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota")
    r = kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota kappa")
    assert r["action"] == "merged"
    # original marked superseded in the index
    assert kb.store.nearest(FakeEmbedder().embed(["alpha beta gamma delta epsilon zeta eta theta iota"])[0],
                            "global", k=10)  # still returns only active rows
    assert all(f.id != first["id"] for f, _ in kb.store.nearest(
        FakeEmbedder().embed(["alpha"])[0], "global", k=10))

def test_scratch_not_written_to_markdown(tmp_path):
    kb = build(tmp_path)
    r = kb.write("agent:claude:scratch", "ephemeral note here")
    assert r["path"] is None
    assert not (tmp_path / "memory").exists() or not list((tmp_path / "memory").rglob("*.md"))

def test_malformed_scope_rejected(tmp_path):
    kb = build(tmp_path)
    with pytest.raises(ValueError):
        kb.write("nonsense", "x")

def test_markdown_survives_db_down(tmp_path):
    class BrokenStore(InMemoryVectorStore):
        def upsert(self, fact, vector):
            raise RuntimeError("db down")
    cfg = Config(repo_path=tmp_path, db_url="x")
    kb = KnowledgeBase(BrokenStore(FakeEmbedder()), FakeEmbedder(), tmp_path, cfg,
                       clock=lambda: FIXED)
    r = kb.write("global", "alpha beta")
    assert Path(r["path"]).exists()           # truth persisted
    from kb.markdown import read_pending_markers
    assert r["id"] in read_pending_markers(tmp_path)

def test_mark_superseded_failure_is_not_swallowed(tmp_path):
    import pytest
    from kb.markdown import read_pending_markers
    class FlakyStore(InMemoryVectorStore):
        def mark_superseded(self, old_id, new_id):
            raise RuntimeError("supersede failed")
    cfg = Config(repo_path=tmp_path, db_url="x")
    kb = KnowledgeBase(FlakyStore(FakeEmbedder()), FakeEmbedder(), tmp_path, cfg,
                       clock=lambda: FIXED)
    kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota")
    with pytest.raises(RuntimeError):
        kb.write("global", "alpha beta gamma delta epsilon zeta eta theta iota kappa")
    # upsert succeeded, so NO misleading pending marker should be written
    assert read_pending_markers(tmp_path) == []
