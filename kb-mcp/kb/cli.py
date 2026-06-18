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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kb")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reindex", help="rebuild the index from markdown")
    sub.add_parser("lint", help="health-check tags and index state")
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
    return 1


if __name__ == "__main__":
    sys.exit(main())
