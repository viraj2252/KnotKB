from datetime import datetime, timezone
from kb.models import Fact
from tests.fakes import FakeReranker

def f(fid, content):
    return Fact(id=fid, scope="global", content=content,
                ts=datetime(2026, 1, 1, tzinfo=timezone.utc))

def test_fake_reranker_orders_by_query_overlap():
    cands = [(f("a", "alpha beta"), 0.1),
             (f("b", "alpha beta gamma delta"), 0.1),
             (f("c", "zeta"), 0.9)]
    out = FakeReranker().rerank("alpha beta gamma", cands)
    assert [fact.id for fact, _ in out] == ["b", "a", "c"]  # most query-overlap first
    assert all(isinstance(s, float) for _, s in out)

def test_fake_reranker_empty():
    assert FakeReranker().rerank("q", []) == []
