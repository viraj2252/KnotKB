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
        if not auth.startswith("Bearer ") or auth[len("Bearer "):] != self.key:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_kb(config: Config) -> KnowledgeBase:
    store = PgVectorStore(connect(config.db_url), dim=config.embed_dim)
    store.ensure_schema()
    embedder = FastEmbedder(model=config.embed_model, dim=config.embed_dim)
    return KnowledgeBase(store, embedder, config.repo_path, config)


def create_app(config: Config):
    from mcp.server.fastmcp import FastMCP

    kb = build_kb(config)
    mcp = FastMCP("kb")

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

    app = mcp.streamable_http_app()

    async def health(request):
        return PlainTextResponse("healthy")

    app.router.routes.append(Route("/health", health))
    app.add_middleware(BearerAuthMiddleware, key=config.mcp_key)
    return app


app = create_app(Config.from_env(os.environ)) if os.environ.get("KB_DB_URL") else None
