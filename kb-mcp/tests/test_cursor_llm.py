import sys
import types

import pytest


def install_fake_cursor_sdk(monkeypatch, reply="fake answer"):
    """Install a fake cursor_sdk module; returns the list of recorded calls."""
    calls = []
    mod = types.ModuleType("cursor_sdk")

    class LocalAgentOptions:
        def __init__(self, cwd=None):
            self.cwd = cwd

    class AgentOptions:
        def __init__(self, model=None, api_key=None, local=None):
            self.model, self.api_key, self.local = model, api_key, local

    class _Result:
        def __init__(self, text):
            self.result = text

    class Agent:
        @staticmethod
        def prompt(prompt, options):
            calls.append((prompt, options))
            return _Result(reply)

    mod.Agent, mod.AgentOptions, mod.LocalAgentOptions = Agent, AgentOptions, LocalAgentOptions
    monkeypatch.setitem(sys.modules, "cursor_sdk", mod)
    return calls


MESSAGES = [{"role": "system", "content": "You answer strictly from context."},
            {"role": "user", "content": "Question: what is alpha?"}]


def test_complete_flattens_messages_and_returns_text(monkeypatch):
    calls = install_fake_cursor_sdk(monkeypatch, reply="alpha is [1]")
    from kb.cursor_llm import CursorAgentClient
    out = CursorAgentClient("crsr_k").complete(MESSAGES, "composer-2.5")
    assert out == "alpha is [1]"
    prompt, options = calls[0]
    assert "You answer strictly from context." in prompt
    assert "Question: what is alpha?" in prompt


def test_complete_prepends_no_tools_instruction(monkeypatch):
    calls = install_fake_cursor_sdk(monkeypatch)
    from kb.cursor_llm import CursorAgentClient
    CursorAgentClient("crsr_k").complete(MESSAGES, "composer-2.5")
    prompt, _ = calls[0]
    assert prompt.startswith("Answer directly as plain text.")
    assert "Do not use any tools" in prompt


def test_complete_passes_model_key_and_empty_workspace(monkeypatch, tmp_path):
    calls = install_fake_cursor_sdk(monkeypatch)
    from kb.cursor_llm import CursorAgentClient
    ws = tmp_path / "empty"
    ws.mkdir()
    CursorAgentClient("crsr_k", workspace=str(ws)).complete(MESSAGES, "composer-2.5")
    _, options = calls[0]
    assert options.model == "composer-2.5"
    assert options.api_key == "crsr_k"
    assert options.local.cwd == str(ws)


def test_default_workspace_is_fresh_temp_dir(monkeypatch):
    install_fake_cursor_sdk(monkeypatch)
    from kb.cursor_llm import CursorAgentClient
    import os
    c = CursorAgentClient("crsr_k")
    assert os.path.isdir(c.workspace)
    assert os.listdir(c.workspace) == []
    assert "kb-cursor-" in c.workspace


def test_missing_sdk_raises_actionable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "cursor_sdk", None)  # import -> ImportError
    from kb.cursor_llm import CursorAgentClient
    with pytest.raises(RuntimeError) as ei:
        CursorAgentClient("crsr_k")
    msg = str(ei.value)
    assert "KB_EXTRAS=cursor" in msg
    assert "kb-mcp[cursor]" in msg
