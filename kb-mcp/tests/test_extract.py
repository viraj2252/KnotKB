from datetime import datetime, timezone
from pathlib import Path
from kb.config import Config
from kb.models import Fact
from kb.markdown import write_fact, markdown_to_fact, read_all_facts
from kb.extract import (parse_entities_json, upsert_entity, load_entities,
                        cache_and_link, extract_over_facts)
from tests.fakes import FakeLLM

TYPES = ("person", "company", "project", "topic")


def test_parse_entities_json_tolerant():
    out = parse_entities_json('noise [{"name":"VJ","type":"person","canonical":"Viraj","aliases":["VJ"]}] tail', TYPES)
    assert out == [{"name": "Viraj", "type": "person", "aliases": ["VJ"]}]
    assert parse_entities_json("not json", TYPES) == []
    assert parse_entities_json('[{"name":"X","type":"bogus"}]', TYPES) == []  # bad type dropped


def test_upsert_entity_creates_then_dedups(tmp_path):
    existing = load_entities(tmp_path)
    s1 = upsert_entity(tmp_path, "Flintt", "project", ["Flint"], existing)
    assert s1 == "flintt"
    assert (tmp_path / "entities" / "flintt.md").exists()
    # a later mention by a known alias must NOT create a new page
    s2 = upsert_entity(tmp_path, "Flint", "project", [], existing)
    assert s2 == "flintt"
    assert sorted(p.name for p in (tmp_path / "entities").glob("*.md")) == ["flintt.md"]
    import yaml
    page = yaml.safe_load((tmp_path / "entities" / "flintt.md").read_text().split("---")[1])
    assert "Flint" in page["aliases"]  # matched alias must survive the merge


def test_parse_entities_json_ignores_trailing_brackets():
    out = parse_entities_json('[{"name":"X","type":"person"}] (ref [1])', TYPES)
    assert out == [{"name": "X", "type": "person", "aliases": []}]


def test_cache_and_link_idempotent(tmp_path):
    ts = datetime(2026, 6, 21, tzinfo=timezone.utc)
    from kb.util import content_hash, make_id
    f = Fact(id=make_id(ts, content_hash("x")), scope="project:flintt",
             content="Flintt ships campaigns.", ts=ts, content_hash=content_hash("x"))
    write_fact(tmp_path, f)
    cache_and_link(f, ["flintt"])
    again = markdown_to_fact(Path(f.path).read_text(), f.path)
    assert again.entities == ["flintt"] and again.extracted is True
    assert "Entities: [[flintt]]" in again.content
    # re-applying doesn't duplicate the line
    cache_and_link(again, ["flintt"])
    assert again.content.count("Entities: [[flintt]]") == 1


def test_extract_over_facts_runs_once_and_caps(tmp_path):
    ts = datetime(2026, 6, 21, tzinfo=timezone.utc)
    from kb.util import content_hash, make_id
    for i in range(3):
        c = f"Flintt fact number {i}"
        write_fact(tmp_path, Fact(id=make_id(ts, content_hash(c)) + str(i), scope="project:flintt",
                                  content=c, ts=ts, content_hash=content_hash(c)))
    cfg = Config(repo_path=tmp_path, db_url="x", extract_max_facts=2)
    llm = FakeLLM()
    counts = extract_over_facts(tmp_path, llm, cfg)
    assert counts["facts_extracted"] == 2          # cap respected
    assert len(llm.calls) == 2
    # second run: the 2 done facts are skipped (extracted flag); only the 3rd is processed
    llm2 = FakeLLM()
    counts2 = extract_over_facts(tmp_path, llm2, cfg)
    assert counts2["facts_extracted"] == 1
    assert len(llm2.calls) == 1
