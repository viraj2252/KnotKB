from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from kb.server import BearerAuthMiddleware

def build_app(key):
    async def ok(request): return PlainTextResponse("ok")
    async def health(request): return PlainTextResponse("healthy")
    app = Starlette(routes=[Route("/mcp", ok), Route("/health", health)])
    app.add_middleware(BearerAuthMiddleware, key=key)
    return TestClient(app)

def test_missing_token_rejected():
    c = build_app("secret")
    assert c.get("/mcp").status_code == 401

def test_wrong_token_rejected():
    c = build_app("secret")
    assert c.get("/mcp", headers={"Authorization": "Bearer nope"}).status_code == 401

def test_correct_token_allowed():
    c = build_app("secret")
    assert c.get("/mcp", headers={"Authorization": "Bearer secret"}).status_code == 200

def test_health_is_open():
    c = build_app("secret")
    assert c.get("/health").status_code == 200
