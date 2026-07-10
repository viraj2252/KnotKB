import argparse
import os
import sys

from kb.config import Config
from kb.reindex import reindex


def _load():
    """Build (config, store, embedder) from env. Overridden in tests."""
    from kb.db import PgVectorStore, connect
    from kb.embeddings import FastEmbedder
    cfg = Config.from_env(os.environ)
    store = PgVectorStore(connect(cfg.db_url), dim=cfg.embed_dim)
    store.ensure_schema()
    return cfg, store, FastEmbedder(model=cfg.embed_model, dim=cfg.embed_dim)


_LLM_DISABLED_HINT = ("LLM disabled: set KB_SYNTH_BASE_URL, or KB_SYNTH_PROVIDER=cursor "
                      "with CURSOR_API_KEY (see docs/SETUP.md)")


def _llm(cfg):
    from kb.synth import build_llm
    return build_llm(cfg)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kb")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reindex", help="rebuild the index from markdown")
    sub.add_parser("lint", help="health-check tags and index state")
    cons = sub.add_parser("consolidate", help="report KB health; auto-merge near-dups with --apply")
    cons.add_argument("--apply", action="store_true", help="apply safe auto-merges")
    sub.add_parser("extract", help="run LLM entity extraction over un-extracted facts")
    rev = sub.add_parser("review", help="list or accept low-confidence ingest drafts")
    rev.add_argument("--accept", action="store_true", help="promote drafts into the KB")
    rev.add_argument("--source", help="only accept drafts from this source filename")
    ing = sub.add_parser("ingest", help="distill a source file into facts (confidence-gated)")
    ing.add_argument("file")
    ing.add_argument("--scope")
    ing.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    cfg, store, embedder = _load()
    if args.cmd == "reindex":
        n = reindex(store, embedder, cfg.repo_path, cfg)
        print(f"indexed {n} facts")
        return 0
    if args.cmd == "lint":
        from kb.lint import lint_report
        report = lint_report(cfg.repo_path, cfg)
        for a, b in report["tag_drift"]:
            print(f"tag-drift: {a!r} ~ {b!r}")
        for fid in report["pending_reindex"]:
            print(f"pending-reindex: {fid}")
        issues = len(report["tag_drift"]) + len(report["pending_reindex"])
        print(f"{issues} issue(s)")
        return 1 if issues else 0
    if args.cmd == "consolidate":
        from kb.consolidate import consolidate
        try:
            llm = _llm(cfg) if (cfg.extract_enabled or cfg.ingest_enabled) else None
        except RuntimeError as e:
            print(f"warning: {e} — continuing without LLM phases")
            llm = None
        report = consolidate(store, embedder, cfg.repo_path, cfg, apply=args.apply, llm=llm)
        print(f"extracted={report['extracted']['facts_extracted']} "
              f"auto_merged={len(report['auto_merged'])} near_dups={len(report['near_dups'])} "
              f"stale={len(report['stale'])} orphans={len(report['orphans'])} "
              f"tag_drift={len(report['tag_drift'])}")
        report_only = (len(report["near_dups"]) - len(report["auto_merged"])
                       + len(report["stale"]) + len(report["orphans"]) + len(report["tag_drift"]))
        return 1 if report_only else 0
    if args.cmd == "extract":
        from kb.extract import extract_over_facts
        try:
            llm = _llm(cfg)
        except RuntimeError as e:
            print(e)
            return 1
        if llm is None:
            print(_LLM_DISABLED_HINT)
            return 1
        counts = extract_over_facts(cfg.repo_path, llm, cfg)
        print(f"facts_extracted={counts['facts_extracted']} "
              f"entities_created={counts['entities_created']} skipped={counts['skipped']}")
        return 0
    if args.cmd == "review":
        from kb.ingest import list_reviews, accept_reviews
        from kb.store import KnowledgeBase
        if args.accept:
            kb = KnowledgeBase(store, embedder, cfg.repo_path, cfg)
            r = accept_reviews(cfg.repo_path, kb, source=args.source)
            print(f"accepted={r['accepted']} skipped={r['skipped']} remaining={r['remaining']}")
            return 0
        for d in list_reviews(cfg.repo_path):
            print(f"[{d['confidence']}] {d['source']}: {d['content'][:70]} ({d['path']})")
        return 0
    if args.cmd == "ingest":
        from kb.ingest import ingest_file
        from kb.store import KnowledgeBase
        try:
            llm = _llm(cfg)
        except RuntimeError as e:
            print(e)
            return 1
        if llm is None:
            print(_LLM_DISABLED_HINT)
            return 1
        kb = KnowledgeBase(store, embedder, cfg.repo_path, cfg)
        c = ingest_file(args.file, kb, llm, cfg, scope=args.scope, force=args.force)
        print(f"facts_written={c['facts_written']} facts_held={c['facts_held']} skipped={c['skipped']}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
