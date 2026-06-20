from datetime import datetime, timezone
import pytest
from kb.util import content_hash, make_id, validate_scope, is_scratch, scope_dir

def test_content_hash_stable():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("a") != content_hash("b")

def test_make_id_format():
    ts = datetime(2026, 6, 18, 9, 30, 5, tzinfo=timezone.utc)
    cid = make_id(ts, content_hash("x"))
    assert cid.startswith("20260618093005-")
    assert len(cid.split("-")[1]) == 6

@pytest.mark.parametrize("scope", ["global", "project:hermes-test", "agent:claude:scratch"])
def test_valid_scopes(scope):
    validate_scope(scope)  # no raise

@pytest.mark.parametrize("scope", ["", "project:", "agent::scratch", "agent:x", "weird", "project:a:b"])
def test_invalid_scopes_raise(scope):
    with pytest.raises(ValueError):
        validate_scope(scope)

def test_is_scratch():
    assert is_scratch("agent:claude:scratch") is True
    assert is_scratch("global") is False

def test_scope_dir():
    assert scope_dir("global") == "global"
    assert scope_dir("project:hermes-test") == "project/hermes-test"

def test_scope_dir_rejects_scratch():
    with pytest.raises(ValueError):
        scope_dir("agent:claude:scratch")

def test_slugify():
    from kb.util import slugify
    assert slugify("Brand Engagement") == "brand-engagement"
    assert slugify("VJ Kothalawala!") == "vj-kothalawala"
    assert slugify("  ") == "entity"
