import hashlib
import re
from datetime import datetime

_PROJECT_RE = re.compile(r"^project:[A-Za-z0-9._-]+$")
_SCRATCH_RE = re.compile(r"^agent:[A-Za-z0-9._-]+:scratch$")


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def make_id(ts: datetime, chash: str) -> str:
    return ts.strftime("%Y%m%d%H%M%S") + "-" + chash[:6]


def validate_scope(scope: str) -> None:
    if scope == "global" or _PROJECT_RE.match(scope) or _SCRATCH_RE.match(scope):
        return
    raise ValueError(f"malformed scope: {scope!r}")


def is_scratch(scope: str) -> bool:
    return bool(_SCRATCH_RE.match(scope))


def scope_dir(scope: str) -> str:
    validate_scope(scope)
    if scope == "global":
        return "global"
    if scope.startswith("project:"):
        return "project/" + scope.split(":", 1)[1]
    raise ValueError(f"scope has no markdown dir: {scope!r}")
