"""HotItem — the unified internal data contract for the pipeline (source → scoring → archive).

Every data source's fetch() produces a List[HotItem]; the scoring layer only knows this shape,
and the persistence layer lands it into posts_archive (see supabase/migrations/0001_init.sql).
Adding a new data source just means producing HotItems — the pipeline itself doesn't change.

Field correspondence with posts_archive:
  - Full content is NOT in HotItem / DB — it goes to Supabase Storage; posts_archive.full_content_url stores the ref.
  - raw_snippet = lightweight excerpt (kept in DB for direct querying), clipped to SNIPPET_MAX to bound row width.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SCHEMA_VERSION = "v1"
SNIPPET_MAX = 500  # Row-width cap for raw_snippet (lightweight excerpt; full text in Storage)

# Tracking-param prefixes/exact names stripped while building dedup_key
_DROP_QS_PREFIXES = ("utm_",)
_DROP_QS_EXACT = {"fbclid", "gclid", "ref", "ref_src", "ref_url", "spm"}


def canonical_url(url: str) -> str:
    """URL canonicalization (the basis of dedup_key; drops tracking params, normalizes case / trailing slash)."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    q = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith(_DROP_QS_PREFIXES) and k.lower() not in _DROP_QS_EXACT
    ]
    q.sort()
    return urlunsplit((scheme, netloc, path, urlencode(q), ""))


def make_id(source: str, native_id: str) -> str:
    """Stable hash id (source + source-native id). Maps to posts_archive(source, source_native_id)."""
    return hashlib.sha1(f"{source}:{native_id}".encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def clip_snippet(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:SNIPPET_MAX]


@dataclass
class HotItem:
    id: str                       # make_id(source, native_id)
    dedup_key: str                # canonical_url(url)
    title: str
    source: str                   # reddit | product_hunt | xiaohongshu — == sources.source_key
    source_native_id: str         # Source-native id — posts_archive(source, source_native_id) UNIQUE
    url: str
    author: str | None
    published_at: str | None      # ISO8601 + timezone (UTC)
    captured_at: str              # ISO8601 + timezone (UTC)
    lang: str
    media_type: str               # text | image | video | mixed
    raw_metrics: dict             # {likes, comments, saves, upvotes}
    source_native: dict           # Per-source native-metric snapshot (shape may differ by source)
    hot_score: float = 0.0        # 0–100 (normalized within source; filled by scoring layer)
    relevance_score: float = 0.0  # 0–1 (filled by scoring layer)
    tags: list = field(default_factory=list)
    raw_snippet: str = ""         # <=SNIPPET_MAX lightweight excerpt (full text in Storage)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["raw_snippet"] = clip_snippet(d.get("raw_snippet"))
        return d
