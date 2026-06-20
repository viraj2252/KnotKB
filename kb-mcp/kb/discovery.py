from kb.links import fact_slug


def rank_experts(search_results, facts_by_path, index, entity_type, k):
    """Sum each result's score onto the typed entities its fact links to."""
    by_slug = index["by_slug"]
    scores: dict[str, float] = {}
    for r in search_results:
        fact = facts_by_path.get(r["path"])
        if fact is None:
            continue
        for dst in index["forward"].get(fact.id, []):
            ent = by_slug.get(dst)
            if ent is not None and ent.entity_type == entity_type:
                scores[dst] = scores.get(dst, 0.0) + float(r["score"])
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    return [(slug, by_slug[slug]) for slug, _ in ranked if slug in by_slug]
