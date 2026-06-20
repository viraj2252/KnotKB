import re
from pathlib import Path

from kb.models import Fact

_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def parse_wikilinks(text: str) -> list[str]:
    out: list[str] = []
    for raw in _WIKILINK.findall(text or ""):
        target = raw.split("|", 1)[0].strip()
        if target:
            out.append(target)
    return out


def fact_slug(fact: Fact) -> str:
    if fact.slug:
        return fact.slug
    if fact.path:
        return Path(fact.path).stem
    return fact.id


def build_link_index(facts: list[Fact]) -> dict:
    by_slug: dict[str, Fact] = {}
    for f in facts:
        by_slug.setdefault(fact_slug(f), f)
        for a in (f.aliases or []):
            by_slug.setdefault(a, f)

    forward: dict[str, list[str]] = {}
    backlinks: dict[str, list[str]] = {}
    for f in facts:
        targets = parse_wikilinks(f.content)
        forward[f.id] = targets
        for t in targets:
            backlinks.setdefault(t, []).append(f.id)

    def has_inbound(f: Fact) -> bool:
        # Check if this fact's ID appears as a direct wikilink target
        if backlinks.get(f.id):
            return True
        # Check if any of its aliases appear as a wikilink target
        return any(backlinks.get(a) for a in (f.aliases or []))

    orphans = [f.id for f in facts if not has_inbound(f)]
    return {"by_slug": by_slug, "forward": forward,
            "backlinks": backlinks, "orphans": orphans}
