import pytest


def test_app_factory_missing_both_env_vars_raises(monkeypatch):
    monkeypatch.delenv("KB_REPO_PATH", raising=False)
    monkeypatch.delenv("KB_DB_URL", raising=False)
    from kb.server import app_factory
    with pytest.raises(RuntimeError) as ei:
        app_factory()
    msg = str(ei.value)
    assert "KB_REPO_PATH" in msg
    assert "KB_DB_URL" in msg
    assert ".env" in msg


def test_app_factory_names_only_the_missing_var(monkeypatch):
    monkeypatch.setenv("KB_REPO_PATH", "/kb")
    monkeypatch.delenv("KB_DB_URL", raising=False)
    from kb.server import app_factory
    with pytest.raises(RuntimeError) as ei:
        app_factory()
    msg = str(ei.value)
    assert "KB_DB_URL" in msg
    assert "KB_REPO_PATH" not in msg


def test_app_factory_treats_empty_value_as_missing(monkeypatch):
    monkeypatch.setenv("KB_REPO_PATH", "/kb")
    monkeypatch.setenv("KB_DB_URL", "")
    from kb.server import app_factory
    with pytest.raises(RuntimeError) as ei:
        app_factory()
    assert "KB_DB_URL" in str(ei.value)
