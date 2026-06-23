from pathlib import Path
from kb.ingest import parse_facts_json, read_source_meta, mark_ingested, build_ingest_messages

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
