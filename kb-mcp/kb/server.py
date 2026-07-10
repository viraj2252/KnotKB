import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from kb.config import Config
from kb.db import PgVectorStore, connect
from kb.embeddings import FastEmbedder
from kb.store import KnowledgeBase


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, key: str) -> None:
        super().__init__(app)
        self.key = key

    async def dispatch(self, request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if not auth.startswith("Bearer ") or not hmac.compare_digest(token, self.key):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_kb(config: Config) -> KnowledgeBase:
    store = PgVectorStore(connect(config.db_url), dim=config.embed_dim)
    store.ensure_schema()
    embedder = FastEmbedder(model=config.embed_model, dim=config.embed_dim)
    reranker = None
    if config.rerank_enabled:
        from kb.rerank import FastReranker
        reranker = FastReranker(model=config.rerank_model)
    return KnowledgeBase(store, embedder, config.repo_path, config, reranker=reranker)


def create_app(config: Config):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    kb = build_kb(config)
    # The streamable-HTTP transport's DNS-rebinding protection rejects any
    # request whose Host header isn't an allowlisted localhost (returns 421
    # Misdirected Request). Hermes reaches us by service name (kb-mcp:8077)
    # over the bridge network, so that check would block it. We disable it
    # because our own bearer-token auth (BearerAuthMiddleware) is the real
    # gate and the server is bound to the private network / 127.0.0.1.
    mcp = FastMCP("kb", transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))

    @mcp.tool()
    def memory_write(scope: str, content: str, tags: list[str] | None = None,
                     source: str | None = None) -> dict:
        """Write a fact to the knowledge base. Returns {id, path, action}."""
        return kb.write(scope, content, tags=tags, source=source)

    @mcp.tool()
    def memory_search(query: str, scope=None, tags: list[str] | None = None,
                      k: int = 8) -> list[dict]:
        """Search the knowledge base. scope: str | list[str] | None (defaults to ['global'])."""
        return kb.search(query, scope=scope, tags=tags, k=k)

    @mcp.tool()
    def get_backlinks(slug: str) -> list[dict]:
        """List facts/pages that link to the given slug via [[wikilinks]]."""
        return kb.get_backlinks(slug)

    @mcp.tool()
    def get_links(slug: str) -> list[dict]:
        """List outgoing [[wikilinks]] from the page/fact with the given slug."""
        return kb.get_links(slug)

    @mcp.tool()
    def find_experts(query: str, entity_type: str = "person", k: int = 5, scope=None) -> list[dict]:
        """Find the entities (default people) most associated with facts matching the query."""
        return kb.find_experts(query, entity_type=entity_type, k=k, scope=scope)

    @mcp.tool()
    def get_entity(slug: str) -> dict:
        """Get an entity page plus the facts that mention it and co-occurring entities."""
        return kb.get_entity(slug)

    @mcp.tool()
    def find_orphans() -> dict:
        """List facts with no inbound links and entity pages mentioned <=1x."""
        return kb.find_orphans()

    @mcp.tool()
    def ask(question: str, scope=None, k: int | None = None) -> dict:
        """Answer a question from the KB with cited sources. Returns {answer, citations, used_facts}."""
        from kb.synth import synthesize, synth_configured, build_llm
        if not synth_configured(config):
            return {"error": "LLM synthesis not configured: set KB_SYNTH_BASE_URL, "
                             "or KB_SYNTH_PROVIDER=cursor with CURSOR_API_KEY "
                             "(see docs/SETUP.md, LLM backend section)"}
        try:
            llm = build_llm(config)
        except RuntimeError as e:
            return {"error": str(e)}
        return synthesize(kb, question, llm, scope=scope, k=k)

    app = mcp.streamable_http_app()

    async def health(request):
        return PlainTextResponse("healthy")

    app.router.routes.append(Route("/health", health))
    app.add_middleware(BearerAuthMiddleware, key=config.mcp_key)
    return app


def app_factory():
    """Uvicorn factory: `uvicorn --factory kb.server:app_factory`."""
    missing = [k for k in ("KB_REPO_PATH", "KB_DB_URL") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            "kb-mcp cannot start: missing required env var(s) " + ", ".join(missing)
            + ". Copy .env.example to .env and fill them in (see docs/SETUP.md)."
        )
    return create_app(Config.from_env(os.environ))
