from datetime import datetime, timezone
from kb.config import Config
from kb.store import KnowledgeBase
from kb.extract import write_entity_page
from tests.fakes import FakeEmbedder, InMemoryVectorStore, FakeReranker

FIXED = datetime(2026, 6, 21, tzinfo=timezone.utc)


def build(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg,
                         clock=lambda: FIXED, reranker=FakeReranker())


def test_find_experts_ranks_entity_in_relevant_facts(tmp_path):
    kb = build(tmp_path)
    write_entity_page(tmp_path, "anna", "person", [], "Person: Anna")
    kb.write("project:flintt", "Anna owns brand engagement testing. Entities: [[anna]]")
    kb.write("project:flintt", "unrelated note about billing")
    experts = kb.find_experts("brand engagement testing", entity_type="person", k=5,
                              scope="project:flintt")
    assert experts and experts[0]["slug"] == "anna"


def test_get_entity_returns_mentions(tmp_path):
    kb = build(tmp_path)
    write_entity_page(tmp_path, "flintt", "project", [], "Project: Flintt")
    kb.write("project:flintt", "Flintt ships campaigns. Entities: [[flintt]]")
    ent = kb.get_entity("flintt")
    assert ent["entity"]["slug"] == "flintt"
    assert any("ships campaigns" in m["content"] for m in ent["mentions"])


def test_find_orphans_flags_unmentioned_entity(tmp_path):
    kb = build(tmp_path)
    write_entity_page(tmp_path, "ghost", "person", [], "Person: Ghost")  # never mentioned
    out = kb.find_orphans()
    assert any(e["slug"] == "ghost" for e in out["entities"])
