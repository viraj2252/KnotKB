from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    repo_path: Path
    db_url: str
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    dedup_merge: float = 0.92
    dedup_skip: float = 0.98
    scratch_ttl_seconds: int = 86400
    mcp_key: str = ""
    mcp_port: int = 8077
    index_sources: bool = False
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-base"
    rerank_candidates: int = 30
    synth_base_url: str = "http://claude-proxy:8000/v1"
    synth_model: str = "claude-sonnet-4-6"
    synth_key: str = ""
    synth_max_facts: int = 8
    stale_days: int = 180
    automerge: float = 0.97

    @staticmethod
    def from_env(env: Mapping[str, str]) -> "Config":
        def flag(name: str, default: bool) -> bool:
            v = env.get(name)
            return default if v is None else v.strip().lower() in ("1", "true", "yes")

        return Config(
            repo_path=Path(env["KB_REPO_PATH"]),
            db_url=env["KB_DB_URL"],
            embed_model=env.get("KB_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
            embed_dim=int(env.get("KB_EMBED_DIM", "384")),
            dedup_merge=float(env.get("KB_DEDUP_MERGE", "0.92")),
            dedup_skip=float(env.get("KB_DEDUP_SKIP", "0.98")),
            scratch_ttl_seconds=int(env.get("KB_SCRATCH_TTL_SECONDS", "86400")),
            mcp_key=env.get("KB_MCP_KEY", ""),
            mcp_port=int(env.get("KB_MCP_PORT", "8077")),
            index_sources=flag("KB_INDEX_SOURCES", False),
            rerank_enabled=flag("KB_RERANK_ENABLED", True),
            rerank_model=env.get("KB_RERANK_MODEL", "BAAI/bge-reranker-base"),
            rerank_candidates=int(env.get("KB_RERANK_CANDIDATES", "30")),
            synth_base_url=env.get("KB_SYNTH_BASE_URL", "http://claude-proxy:8000/v1"),
            synth_model=env.get("KB_SYNTH_MODEL", "claude-sonnet-4-6"),
            synth_key=env.get("KB_SYNTH_KEY", ""),
            synth_max_facts=int(env.get("KB_SYNTH_MAX_FACTS", "8")),
            stale_days=int(env.get("KB_STALE_DAYS", "180")),
            automerge=float(env.get("KB_AUTOMERGE", "0.97")),
        )
