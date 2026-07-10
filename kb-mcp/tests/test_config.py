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

def test_spec_b_defaults():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "x"})
    assert cfg.extract_enabled is True
    assert cfg.extract_model == ""
    assert cfg.extract_max_facts == 50
    assert cfg.entity_types == ("person", "company", "project", "topic")
    assert cfg.backlink_boost == 0.0

def test_spec_b_overrides():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "x",
                           "KB_EXTRACT_ENABLED": "false", "KB_EXTRACT_MAX_FACTS": "5",
                           "KB_ENTITY_TYPES": "person,project", "KB_BACKLINK_BOOST": "0"})
    assert cfg.extract_enabled is False
    assert cfg.extract_max_facts == 5
    assert cfg.entity_types == ("person", "project")
    assert cfg.backlink_boost == 0.0

def test_spec_c_defaults():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "x"})
    assert cfg.ingest_enabled is True
    assert cfg.ingest_model == ""
    assert cfg.ingest_max_sources == 10
    assert cfg.ingest_confidence == 85

def test_spec_c_overrides():
    cfg = Config.from_env({"KB_REPO_PATH": "/kb", "KB_DB_URL": "x",
                           "KB_INGEST_ENABLED": "false", "KB_INGEST_MAX_SOURCES": "3",
                           "KB_INGEST_CONFIDENCE": "70"})
    assert cfg.ingest_enabled is False
    assert cfg.ingest_max_sources == 3
    assert cfg.ingest_confidence == 70

def test_provider_defaults_to_openai(tmp_path):
    cfg = Config.from_env({"KB_REPO_PATH": str(tmp_path), "KB_DB_URL": "x"})
    assert cfg.synth_provider == "openai"
    assert cfg.cursor_api_key == ""

def test_provider_cursor_parsed(tmp_path):
    cfg = Config.from_env({"KB_REPO_PATH": str(tmp_path), "KB_DB_URL": "x",
                           "KB_SYNTH_PROVIDER": " Cursor ",
                           "CURSOR_API_KEY": "crsr_test"})
    assert cfg.synth_provider == "cursor"
    assert cfg.cursor_api_key == "crsr_test"
