from datetime import datetime, timezone
from kb.models import Fact
from kb.links import parse_wikilinks, fact_slug, build_link_index

def f(fid, content, slug=None, aliases=(), path=None):
    return Fact(id=fid, scope="global", content=content, slug=slug,
                aliases=list(aliases), path=path,
                ts=datetime(2026, 1, 1, tzinfo=timezone.utc))

def test_parse_wikilinks():
    assert parse_wikilinks("see [[ai-trends]] and [[plans|My Plans]]") == ["ai-trends", "plans"]
    assert parse_wikilinks("none here") == []

def test_fact_slug_precedence():
    assert fact_slug(f("1", "x", slug="explicit")) == "explicit"
    assert fact_slug(f("2", "x", path="/kb/wiki/ai-trends.md")) == "ai-trends"
    assert fact_slug(f("3", "x")) == "3"

def test_build_link_index_backlinks_and_orphans():
    page = f("p", "topic page", slug="ai-trends")          # linked by slug below -> not orphan
    a = f("a", "see [[ai-trends]]")                         # links out; nothing links to it -> orphan
    b = f("b", "links via alias [[AI Trends]]")            # links out; nothing links to it -> orphan
    target = f("t", "the target", slug="ai-trends2", aliases=["AI Trends"])  # linked by alias -> not orphan
    idx = build_link_index([page, a, b, target])
    assert "a" in idx["backlinks"]["ai-trends"]
    assert "b" in idx["backlinks"]["AI Trends"]
    # page is targeted by [[ai-trends]] (its slug) -> NOT an orphan
    assert "p" not in idx["orphans"]
    # target is targeted by [[AI Trends]] (its alias) -> NOT an orphan
    assert "t" not in idx["orphans"]
    # a and b have no inbound links -> orphans
    assert "a" in idx["orphans"]
    assert "b" in idx["orphans"]
