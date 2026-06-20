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


def test_frontmatter_without_ts_falls_back_to_mtime(tmp_path):
    # Obsidian stamps `created`/`updated` (not our `ts`) onto pages — must not yield ts=None
    d = tmp_path / "wiki"; d.mkdir()
    f = d / "page.md"
    f.write_text("---\ncreated: 2026-06-20T23:38\nupdated: 2026-06-20T23:40\n---\n# Page\nbody")
    facts = read_all_facts(tmp_path)
    page = [x for x in facts if x.path and x.path.endswith("page.md")][0]
    assert page.ts is not None

def test_slug_aliases_round_trip(tmp_path):
    f = make_fact()
    f.slug = "ai-trends"
    f.aliases = ["AI Trends", "ml-trends"]
    back = markdown_to_fact(fact_to_markdown(f), path="x.md")
    assert back.slug == "ai-trends"
    assert back.aliases == ["AI Trends", "ml-trends"]

def test_plain_page_slug_is_stem(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "ai-trends.md").write_text("# AI trends\nbody")
    facts = read_all_facts(tmp_path)
    page = [f for f in facts if f.source and f.source.endswith("ai-trends.md")][0]
    assert page.slug == "ai-trends"


def test_obsidian_normalized_datetime_ts_is_parsed(tmp_path):
    # Obsidian unquotes our ISO ts string, so YAML loads it as a native datetime/date.
    # markdown_to_fact must accept both without crashing.
    dt = markdown_to_fact("---\nid: a\nscope: global\nts: 2026-06-20 15:05:40+00:00\n---\nbody", "a.md")
    assert dt.ts is not None and dt.ts.year == 2026
    d = markdown_to_fact("---\nid: b\nscope: global\nts: 2026-06-20\n---\nbody", "b.md")
    assert d.ts is not None and d.ts.tzinfo is not None  # date coerced to tz-aware datetime
