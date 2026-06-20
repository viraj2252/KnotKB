from pathlib import Path
from kb.config import Config

def test_defaults_applied_with_minimal_env():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "postgresql://x"})
    assert cfg.repo_path == Path("/kb")
    assert cfg.db_url == "postgresql://x"
    assert cfg.embed_model == "BAAI/bge-small-en-v1.5"
    assert cfg.embed_dim == 384
    assert cfg.dedup_merge == 0.92
    assert cfg.dedup_skip == 0.98
    assert cfg.scratch_ttl_seconds == 86400
    assert cfg.mcp_port == 8077
    assert cfg.mcp_key == ""
    assert cfg.index_sources is False

def test_overrides_from_env():
    cfg = Config.from_env({
        "KB_REPO_PATH": "/kb", "KB_DB_URL": "postgresql://x",
        "KB_DEDUP_MERGE": "0.8", "KB_DEDUP_SKIP": "0.95",
        "KB_SCRATCH_TTL_SECONDS": "60", "KB_MCP_PORT": "9000",
        "KB_MCP_KEY": "secret", "KB_INDEX_SOURCES": "true",
    })
    assert cfg.dedup_merge == 0.8
    assert cfg.dedup_skip == 0.95
    assert cfg.scratch_ttl_seconds == 60
    assert cfg.mcp_port == 9000
    assert cfg.mcp_key == "secret"
    assert cfg.index_sources is True

def test_spec_a_defaults():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "postgresql://x"})
    assert cfg.rerank_enabled is True
    assert cfg.rerank_model == "BAAI/bge-reranker-base"
    assert cfg.rerank_candidates == 30
    assert cfg.synth_base_url == "http://claude-proxy:8000/v1"
    assert cfg.synth_model == "claude-sonnet-4-6"
    assert cfg.synth_key == ""
    assert cfg.synth_max_facts == 8
    assert cfg.stale_days == 180
    assert cfg.automerge == 0.97

def test_spec_a_overrides():
    cfg = Config.from_env({
        "KB_REPO_PATH": "/kb", "KB_DB_URL": "postgresql://x",
        "KB_RERANK_ENABLED": "false", "KB_RERANK_CANDIDATES": "10",
        "KB_SYNTH_MODEL": "claude-opus-4-8", "KB_AUTOMERGE": "0.95",
        "KB_STALE_DAYS": "30",
    })
    assert cfg.rerank_enabled is False
    assert cfg.rerank_candidates == 10
    assert cfg.synth_model == "claude-opus-4-8"
    assert cfg.automerge == 0.95
    assert cfg.stale_days == 30
