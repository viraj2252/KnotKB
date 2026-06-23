from datetime import datetime, timezone
from pathlib import Path
from kb.config import Config
from kb.store import KnowledgeBase
from kb.ingest import parse_facts_json, read_source_meta, mark_ingested, build_ingest_messages, ingest_file, write_review_draft
from tests.fakes import FakeEmbedder, InMemoryVectorStore, FakeLLM

FIXED = datetime(2026, 6, 21, tzinfo=timezone.utc)

def _kb(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED), cfg

def test_build_ingest_messages_has_system_and_content():
    msgs = build_ingest_messages("Anna owns testing.")
    assert msgs[0]["role"] == "system" and "JSON" in msgs[0]["content"]
    assert "Anna owns testing." in msgs[1]["content"]

def test_parse_facts_json_tolerant_and_confidence_defaults():
    out = parse_facts_json('note [{"content":"A","tags":["t"],"confidence":90},'
                           '{"content":"B"}] trailing')
    assert out[0] == {"content": "A", "tags": ["t"], "confidence": 90}
    assert out[1] == {"content": "B", "tags": [], "confidence": 0}   # missing -> 0
    assert parse_facts_json("not json") == []
    assert parse_facts_json('[{"tags":["x"]}]') == []                # no content dropped

def test_parse_facts_json_clamps_confidence():
    assert parse_facts_json('[{"content":"A","confidence":250}]')[0]["confidence"] == 100
    assert parse_facts_json('[{"content":"A","confidence":"bad"}]')[0]["confidence"] == 0

def test_read_source_meta_and_mark_ingested(tmp_path):
    p = tmp_path / "s.md"
    p.write_text("---\nkb_scope: project:flintt\ncreated: 2026-06-21\n---\n\nbody text here")
    meta, body = read_source_meta(str(p))
    assert meta["kb_scope"] == "project:flintt"
    assert body.strip() == "body text here"
    mark_ingested(str(p))
    meta2, _ = read_source_meta(str(p))
    assert meta2["kb_ingested"] is True and meta2["kb_scope"] == "project:flintt"

def test_mark_ingested_adds_frontmatter_when_absent(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("just a plain note, no front-matter")
    mark_ingested(str(p))
    meta, body = read_source_meta(str(p))
    assert meta["kb_ingested"] is True
    assert "just a plain note" in body


def test_ingest_file_splits_by_confidence(tmp_path):
    kb, cfg = _kb(tmp_path)
    src = tmp_path / "sources"; src.mkdir()
    f = src / "note.md"
    f.write_text("---\nkb_scope: project:flintt\n---\n\nAnna owns testing.")
    llm = FakeLLM(reply='[{"content":"Anna owns testing","tags":["qa"],"confidence":92},'
                        '{"content":"Maybe Anna prefers dark mode","tags":[],"confidence":40}]')
    counts = ingest_file(str(f), kb, llm, cfg)
    assert counts == {"facts_written": 1, "facts_held": 1, "skipped": 0}
    # high-confidence fact written under the directive scope
    written = list((tmp_path / "memory" / "project" / "flintt").glob("*.md"))
    assert written and "Anna owns testing" in written[0].read_text()
    # low-confidence fact held in review/, NOT in memory/
    drafts = list((tmp_path / "review").glob("*.md"))
    assert drafts and "dark mode" in drafts[0].read_text()
    # source marked ingested
    assert read_source_meta(str(f))[0]["kb_ingested"] is True


def test_ingest_file_skips_when_already_ingested(tmp_path):
    kb, cfg = _kb(tmp_path)
    f = tmp_path / "s.md"
    f.write_text("---\nkb_scope: global\nkb_ingested: true\n---\n\nbody")
    llm = FakeLLM(reply='[{"content":"X","confidence":99}]')
    assert ingest_file(str(f), kb, llm, cfg) == {"facts_written": 0, "facts_held": 0, "skipped": 1}
    assert llm.calls == []                          # no LLM call when skipping
    assert ingest_file(str(f), kb, llm, cfg, force=True)["facts_written"] == 1  # --force overrides


def test_ingest_file_scope_precedence(tmp_path):
    kb, cfg = _kb(tmp_path)
    f = tmp_path / "s.md"
    f.write_text("---\nkb_scope: project:flintt\n---\n\nbody")
    llm = FakeLLM(reply='[{"content":"X","confidence":99}]')
    ingest_file(str(f), kb, llm, cfg, scope="global")   # explicit --scope wins
    assert list((tmp_path / "memory" / "global").glob("*.md"))


def test_ingest_file_llm_error_does_not_mark_ingested(tmp_path):
    kb, cfg = _kb(tmp_path)
    f = tmp_path / "s.md"
    f.write_text("---\nkb_scope: global\n---\n\nbody")
    class Boom:
        def complete(self, m, model): raise RuntimeError("proxy down")
    assert ingest_file(str(f), kb, Boom(), cfg) == {"facts_written": 0, "facts_held": 0, "skipped": 1}
    assert "kb_ingested" not in read_source_meta(str(f))[0]   # left for retry


def test_list_and_accept_reviews(tmp_path):
    kb, cfg = _kb(tmp_path)
    from kb.ingest import write_review_draft, list_reviews, accept_reviews
    write_review_draft(tmp_path, "global", "draft fact one", ["t"], 40, "n.md", FIXED)
    write_review_draft(tmp_path, "global", "draft fact two", [], 50, "n.md", FIXED)
    listed = list_reviews(tmp_path)
    assert len(listed) == 2 and any("draft fact one" in d["content"] for d in listed)
    res = accept_reviews(tmp_path, kb)
    assert res["accepted"] == 2
    assert not list((tmp_path / "review").glob("*.md"))           # drafts removed
    assert len(list((tmp_path / "memory" / "global").glob("*.md"))) == 2  # promoted

def test_accept_reviews_skips_invalid_scope(tmp_path):
    kb, cfg = _kb(tmp_path)
    from kb.ingest import write_review_draft, accept_reviews
    write_review_draft(tmp_path, "not a scope", "x", [], 40, "n.md", FIXED)
    res = accept_reviews(tmp_path, kb)
    assert res["accepted"] == 0 and res["skipped"] == 1
    assert res["remaining"] == 1   # invalid-scope draft also counts as remaining
    assert list((tmp_path / "review").glob("*.md"))               # draft left in place

def test_accept_reviews_source_filter_leaves_nonmatching(tmp_path):
    kb, cfg = _kb(tmp_path)
    from kb.ingest import write_review_draft, accept_reviews
    write_review_draft(tmp_path, "global", "from A", [], 40, "a.md", FIXED)
    write_review_draft(tmp_path, "global", "from B", [], 40, "b.md", FIXED)
    res = accept_reviews(tmp_path, kb, source="a.md")
    assert res["accepted"] == 1            # only a.md promoted
    assert res["skipped"] == 0             # non-match is NOT a skip
    assert res["remaining"] == 1           # b.md left, counted as remaining
    left = list((tmp_path / "review").glob("*.md"))
    assert len(left) == 1 and "from B" in left[0].read_text()  # b.md draft untouched


def test_ingest_pending_only_opted_in_and_capped(tmp_path):
    kb, cfg = _kb(tmp_path)
    cfg = Config(repo_path=tmp_path, db_url="x", ingest_max_sources=1)
    src = tmp_path / "sources"; src.mkdir()
    (src / "a.md").write_text("---\nkb_scope: global\n---\n\nfact a")
    (src / "b.md").write_text("---\nkb_scope: global\n---\n\nfact b")
    (src / "ref.md").write_text("just reference material, no kb_scope")  # not opted in
    from kb.ingest import ingest_pending_sources
    res = ingest_pending_sources(tmp_path, kb, FakeLLM(reply='[{"content":"X","confidence":99}]'), cfg)
    assert res["sources_ingested"] == 1                  # cap honored
    # ref.md without kb_scope is never touched
    from kb.ingest import read_source_meta
    assert "kb_ingested" not in read_source_meta(str(src / "ref.md"))[0]
