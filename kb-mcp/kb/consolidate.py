from datetime import datetime, timezone, timedelta

from kb.links import build_link_index
from kb.lint import _normalize
from kb.markdown import read_all_facts, set_superseded, append_log


def _cos(u, v):
    return sum(x * y for x, y in zip(u, v))


def consolidate(store, embedder, repo_path, config, apply: bool = False,
                now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    facts = read_all_facts(repo_path, include_sources=False)
    report = {"near_dups": [], "auto_merged": [], "stale": [],
              "orphans": [], "tag_drift": []}
    if not facts:
        return report

    vecs = embedder.embed([f.content for f in facts])
    byid = {f.id: f for f in facts}

    # near-duplicates (same scope), and auto-merge the strict subset
    for i in range(len(facts)):
        for j in range(i + 1, len(facts)):
            if facts[i].scope != facts[j].scope:
                continue
            sim = _cos(vecs[i], vecs[j])
            if sim < config.dedup_merge:
                continue
            a, b = facts[i], facts[j]
            report["near_dups"].append({"a": a.id, "b": b.id, "sim": round(sim, 4)})
            if apply and sim >= config.automerge:
                # keep the newer fact; supersede the older (non-destructive)
                older, newer = sorted([a, b], key=lambda f: f.ts or now)
                if older.superseded_by:
                    continue
                set_superseded(repo_path, older, newer.id)
                store.mark_superseded(older.id, newer.id)
                report["auto_merged"].append({"superseded": older.id, "into": newer.id,
                                              "sim": round(sim, 4)})

    # staleness (report-only)
    cutoff = now - timedelta(days=config.stale_days)
    for f in facts:
        if f.ts and f.ts < cutoff:
            report["stale"].append({"id": f.id, "content": f.content[:80], "ts": f.ts.isoformat()})

    # orphans (report-only)
    idx = build_link_index(facts)
    report["orphans"] = [{"id": i, "content": byid[i].content[:80]}
                         for i in idx["orphans"] if i in byid]

    # tag drift (report-only) — same normalize rule as kb lint
    tags = sorted({t for f in facts for t in f.tags})
    for x in range(len(tags)):
        for y in range(x + 1, len(tags)):
            if tags[x] != tags[y] and _normalize(tags[x]) == _normalize(tags[y]):
                report["tag_drift"].append((tags[x], tags[y]))

    _write_report(repo_path, now, report, apply)
    return report


def _write_report(repo_path, now, report, apply) -> None:
    d = repo_path / ".kb" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{now.date().isoformat()}.md"
    lines = [f"# Consolidation {now.isoformat()} (apply={apply})", ""]
    for key in ("auto_merged", "near_dups", "stale", "orphans", "tag_drift"):
        lines.append(f"## {key} ({len(report[key])})")
        for item in report[key]:
            lines.append(f"- {item}")
        lines.append("")
    p.write_text("\n".join(lines))
    append_log(repo_path,
               f"## [{now.date().isoformat()}] consolidate | apply={apply} | "
               f"merged={len(report['auto_merged'])} dups={len(report['near_dups'])} "
               f"stale={len(report['stale'])} orphans={len(report['orphans'])}")
