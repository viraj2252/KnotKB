from datetime import datetime, timezone
from pathlib import Path
from kb.models import Fact
from kb.markdown import (
    fact_to_markdown, markdown_to_fact, write_fact, read_all_facts,
    append_log, write_pending_marker, read_pending_markers, clear_pending_marker,
)

def make_fact(content="alpha beta", scope="global", tags=("ai-trends",)):
    ts = datetime(2026, 6, 18, 9, 0, 0, tzinfo=timezone.utc)
    from kb.util import content_hash, make_id
    ch = content_hash(content)
    return Fact(id=make_id(ts, ch), scope=scope, content=content,
                tags=list(tags), source="conv", ts=ts, content_hash=ch)

def test_round_trip(tmp_path):
    f = make_fact()
    text = fact_to_markdown(f)
    back = markdown_to_fact(text, path="x.md")
    assert back.id == f.id
    assert back.scope == f.scope
    assert back.content == f.content
    assert back.tags == f.tags
    assert back.source == f.source
    assert back.ts == f.ts
    assert back.content_hash == f.content_hash

def test_write_fact_places_file_by_scope(tmp_path):
    f = make_fact(scope="project:hermes-test")
    p = write_fact(tmp_path, f)
    assert p.exists()
    assert p.parent == tmp_path / "memory" / "project" / "hermes-test"
    assert f.path == str(p)

def test_read_all_facts_collects_memory(tmp_path):
    write_fact(tmp_path, make_fact(content="alpha"))
    write_fact(tmp_path, make_fact(content="beta gamma", scope="project:p"))
    facts = read_all_facts(tmp_path)
    contents = sorted(f.content for f in facts)
    assert contents == ["alpha", "beta gamma"]

def test_read_all_facts_excludes_sources_by_default(tmp_path):
    (tmp_path / "sources").mkdir(parents=True)
    (tmp_path / "sources" / "raw.md").write_text("# raw transcript\nlots of noise")
    write_fact(tmp_path, make_fact(content="alpha"))
    facts = read_all_facts(tmp_path)
    assert all("transcript" not in f.content for f in facts)

def test_append_log(tmp_path):
    append_log(tmp_path, "## [2026-06-18] write | global | alpha")
    assert "alpha" in (tmp_path / "log.md").read_text()

def test_pending_markers(tmp_path):
    write_pending_marker(tmp_path, "id1")
    write_pending_marker(tmp_path, "id2")
    assert sorted(read_pending_markers(tmp_path)) == ["id1", "id2"]
    clear_pending_marker(tmp_path, "id1")
    assert read_pending_markers(tmp_path) == ["id2"]

def test_round_trip_preserves_expires_at():
    f = make_fact()
    f.expires_at = datetime(2026, 6, 19, 9, 0, 0, tzinfo=timezone.utc)
    back = markdown_to_fact(fact_to_markdown(f), path="x.md")
    assert back.expires_at == f.expires_at
