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
    args = parser.parse_args(argv)

    cfg, store, embedder = _load()
    if args.cmd == "reindex":
        n = reindex(store, embedder, cfg.repo_path, cfg)
        print(f"indexed {n} facts")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
