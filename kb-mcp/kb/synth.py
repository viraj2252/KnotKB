import re
from typing import Protocol

from kb.models import Fact

_SYSTEM = (
    "You answer strictly from the numbered context below. Cite the sources you "
    "use as [n] inline, matching the numbers in the context. If the context does "
    "not contain the answer, reply exactly 'insufficient evidence'. Be concise."
)


class LLMClient(Protocol):
    def complete(self, messages: list[dict], model: str) -> str: ...


def synth_configured(config) -> bool:
    """LLM features are enabled iff a synth base URL is set (empty = off)."""
    return bool(config.synth_base_url)


def build_messages(question: str, facts: list[Fact]) -> list[dict]:
    lines = []
    for i, f in enumerate(facts, 1):
        src = f.path or f.source or f.scope
        lines.append(f"[{i}] ({src}) {f.content}")
    user = f"Question: {question}\n\nContext:\n" + "\n".join(lines)
    return [{"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user}]


def parse_citations(answer: str, facts: list[Fact]) -> list[dict]:
    nums = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer or "")})
    cites = []
    for n in nums:
        if 1 <= n <= len(facts):
            f = facts[n - 1]
            cites.append({"n": n, "path": f.path, "scope": f.scope})
    return cites


class OpenAIWireClient:
    """Minimal OpenAI-wire chat client (points at claude-proxy by default)."""

    def __init__(self, base_url: str, key: str = "", timeout: float = 60.0) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.key = key
        self.timeout = timeout

    def complete(self, messages: list[dict], model: str) -> str:
        import httpx
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        resp = httpx.post(self.url, json={"model": model, "messages": messages,
                                          "stream": False},
                          headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def synthesize(kb, question: str, llm: LLMClient, scope=None, k: int | None = None) -> dict:
    k = k if k is not None else kb.config.synth_max_facts
    results = kb.search(question, scope=scope, k=k)
    if not results:
        return {"answer": "insufficient evidence in the knowledge base",
                "citations": [], "used_facts": []}
    facts = [Fact(id="", scope=r["scope"], content=r["content"],
                  source=r["source"], path=r["path"]) for r in results]
    try:
        answer = llm.complete(build_messages(question, facts), kb.config.synth_model)
    except Exception as e:  # proxy down / timeout
        return {"error": f"synthesis failed: {e}"}
    return {"answer": answer, "citations": parse_citations(answer, facts),
            "used_facts": results}
