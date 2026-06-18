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
        )
