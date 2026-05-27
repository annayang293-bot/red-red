"""HotItem —— pipeline 内部统一数据契约(source → scoring → archive)。

每个数据源的 fetch() 都产出 List[HotItem],打分层只认这个结构,入库层把它
落进 posts_archive(见 supabase/migrations/0001_init.sql)。新增数据源只要产出
HotItem 即可接入,主线无需改动。

字段对应 posts_archive:
  - 全文不进 HotItem/DB —— 走 Supabase Storage,posts_archive.full_content_url 存 ref。
  - raw_snippet = 轻量摘要(留 DB 直接查询用),clip 到 SNIPPET_MAX 控制行宽。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SCHEMA_VERSION = "v1"
SNIPPET_MAX = 500  # raw_snippet 行宽上限(轻量摘要;全文走 Storage)

# 规范化 dedup_key 时剔除的跟踪参数前缀/精确名
_DROP_QS_PREFIXES = ("utm_",)
_DROP_QS_EXACT = {"fbclid", "gclid", "ref", "ref_src", "ref_url", "spm"}


def canonical_url(url: str) -> str:
    """URL 规范化(dedup_key 基础;剔除跟踪参数、统一大小写/尾斜杠)。"""
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
    """稳定 hash id(source + 源原生 id)。对应 posts_archive(source, source_native_id)。"""
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
    source: str                   # reddit | product_hunt | xiaohongshu —— == sources.source_key
    source_native_id: str         # 源原生 id —— posts_archive(source, source_native_id) UNIQUE
    url: str
    author: str | None
    published_at: str | None      # ISO8601 + 时区(UTC)
    captured_at: str              # ISO8601 + 时区(UTC)
    lang: str
    media_type: str               # text | image | video | mixed
    raw_metrics: dict             # {likes, comments, saves, upvotes}
    source_native: dict           # 各源原生指标快照(结构允许各源不同)
    hot_score: float = 0.0        # 0–100(来源内归一化,scoring 层填)
    relevance_score: float = 0.0  # 0–1(scoring 层填)
    tags: list = field(default_factory=list)
    raw_snippet: str = ""         # <=SNIPPET_MAX 轻量摘要(全文走 Storage)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["raw_snippet"] = clip_snippet(d.get("raw_snippet"))
        return d
