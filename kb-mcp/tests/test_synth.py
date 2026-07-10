from datetime import datetime, timezone
from kb.config import Config
from kb.store import KnowledgeBase
from kb.synth import build_messages, parse_citations, synthesize
from kb.models import Fact
from tests.fakes import FakeEmbedder, InMemoryVectorStore

FIXED = datetime(2026, 6, 20, tzinfo=timezone.utc)

class FakeLLM:
    def __init__(self, reply="From the notes, X is true [1].", record=None):
        self.reply, self.calls = reply, (record if record is not None else [])
    def complete(self, messages, model):
        self.calls.append((messages, model))
        return self.reply

def build_kb(tmp_path):
    cfg = Config(repo_path=tmp_path, db_url="x")
    emb = FakeEmbedder()
    return KnowledgeBase(InMemoryVectorStore(emb), emb, tmp_path, cfg, clock=lambda: FIXED)

def test_build_messages_numbers_and_tags_sources():
    facts = [Fact(id="1", scope="global", content="alpha", path="/kb/memory/global/1.md")]
    msgs = build_messages("what is alpha?", facts)
    assert msgs[0]["role"] == "system"
    assert "[1]" in msgs[1]["content"] and "alpha" in msgs[1]["content"]

def test_parse_citations_maps_markers():
    facts = [Fact(id="1", scope="global", content="a", path="p1"),
             Fact(id="2", scope="project:x", content="b", path="p2")]
    cites = parse_citations("yes [2] and also [1].", facts)
    assert {c["n"] for c in cites} == {1, 2}
    assert {c["path"] for c in cites} == {"p1", "p2"}

def test_synthesize_happy_path(tmp_path):
    kb = build_kb(tmp_path)
    kb.write("global", "alpha beta gamma fact")
    llm = FakeLLM(reply="alpha beta per the note [1].")
    out = synthesize(kb, "tell me about alpha beta", llm)
    assert out["answer"] == "alpha beta per the note [1]."
    assert out["citations"] and out["citations"][0]["n"] == 1
    assert len(llm.calls) == 1

def test_synthesize_no_facts_skips_llm(tmp_path):
    kb = build_kb(tmp_path)
    llm = FakeLLM()
    out = synthesize(kb, "nothing stored about this", llm)
    assert "insufficient evidence" in out["answer"]
    assert out["citations"] == [] and llm.calls == []

def test_synthesize_llm_error_returns_error(tmp_path):
    kb = build_kb(tmp_path)
    kb.write("global", "alpha beta gamma fact")
    class Boom:
        def complete(self, messages, model): raise RuntimeError("proxy down")
    out = synthesize(kb, "alpha beta", Boom())
    assert "error" in out

def test_openai_wire_client_builds_url():
    from kb.synth import OpenAIWireClient
    c = OpenAIWireClient("http://claude-proxy:8000/v1", "")
    assert c.url == "http://claude-proxy:8000/v1/chat/completions"

def test_synth_configured_true_with_base_url(tmp_path):
    from kb.synth import synth_configured
    assert synth_configured(Config(repo_path=tmp_path, db_url="x")) is True

def test_synth_configured_false_with_empty_base_url(tmp_path):
    from kb.synth import synth_configured
    cfg = Config(repo_path=tmp_path, db_url="x", synth_base_url="")
    assert synth_configured(cfg) is False

def test_synth_configured_cursor_requires_key(tmp_path):
    from kb.synth import synth_configured
    with_key = Config(repo_path=tmp_path, db_url="x", synth_provider="cursor",
                      cursor_api_key="crsr_k", synth_base_url="")
    without_key = Config(repo_path=tmp_path, db_url="x", synth_provider="cursor",
                         synth_base_url="http://claude-proxy:8000/v1")
    assert synth_configured(with_key) is True
    assert synth_configured(without_key) is False

def test_build_llm_openai(tmp_path):
    from kb.synth import build_llm, OpenAIWireClient
    cfg = Config(repo_path=tmp_path, db_url="x")
    assert isinstance(build_llm(cfg), OpenAIWireClient)

def test_build_llm_unconfigured_returns_none(tmp_path):
    from kb.synth import build_llm
    assert build_llm(Config(repo_path=tmp_path, db_url="x", synth_base_url="")) is None

def test_build_llm_cursor(tmp_path, monkeypatch):
    from tests.test_cursor_llm import install_fake_cursor_sdk
    install_fake_cursor_sdk(monkeypatch)
    from kb.synth import build_llm
    from kb.cursor_llm import CursorAgentClient
    cfg = Config(repo_path=tmp_path, db_url="x", synth_provider="cursor",
                 cursor_api_key="crsr_k")
    llm = build_llm(cfg)
    assert isinstance(llm, CursorAgentClient)
    assert llm.api_key == "crsr_k"
