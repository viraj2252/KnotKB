from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Fact:
    id: str
    scope: str
    content: str
    tags: list[str] = field(default_factory=list)
    source: str | None = None
    ts: datetime = None  # type: ignore[assignment]
    content_hash: str = ""
    superseded_by: str | None = None
    path: str | None = None
    expires_at: datetime | None = None
    slug: str | None = None
    aliases: list[str] = field(default_factory=list)
