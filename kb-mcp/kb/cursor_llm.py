import os
import tempfile

_INSTRUCTION = (
    "Answer directly as plain text. Do not use any tools, do not read or "
    "modify files, do not run commands. Respond with the answer only."
)

_workspace: str | None = None


def _shared_workspace() -> str:
    global _workspace
    if _workspace is None or not os.path.isdir(_workspace):
        _workspace = tempfile.mkdtemp(prefix="kb-cursor-")
    return _workspace


class CursorAgentClient:
    """LLMClient adapter: one-shot Cursor agent runs via cursor-sdk.

    Each complete() is a stateless Agent.prompt() with a local runtime whose
    cwd is an empty scratch directory (not the vault or a repo); the prompt
    additionally instructs the agent to answer text-only without tools.
    """

    def __init__(self, api_key: str, workspace: str | None = None) -> None:
        try:
            from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
        except ImportError as e:
            raise RuntimeError(
                "cursor provider selected but cursor-sdk is not installed. "
                "Rebuild the image with KB_EXTRAS=cursor "
                "(or: pip install 'kb-mcp[cursor]')."
            ) from e
        self._agent = Agent
        self._agent_options = AgentOptions
        self._local_options = LocalAgentOptions
        self.api_key = api_key
        self.workspace = workspace or _shared_workspace()

    def complete(self, messages: list[dict], model: str) -> str:
        prompt = "\n\n".join([_INSTRUCTION] + [m["content"] for m in messages])
        result = self._agent.prompt(
            prompt,
            self._agent_options(
                model=model,
                api_key=self.api_key,
                local=self._local_options(cwd=self.workspace),
            ),
        )
        return result.result
