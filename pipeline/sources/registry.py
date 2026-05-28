"""Data-source registry — maps the sources table's source_key/adapter_class to Python classes.

This is the heart of "pluggable". Adding a new source (e.g. Xiaohongshu) is three steps,
nothing else changes:
  1) Write XxxSource(Source) implementing fetch() (producing List[HotItem])
  2) Register one row in SOURCE_REGISTRY below
  3) INSERT one row into the sources table (source_key, adapter_class='XxxSource', enabled, quota_top20)

At runtime the pipeline reads the enabled=TRUE source_key list from the sources table →
get_source() instantiates each → fetch() aggregates them. Registry keys correspond 1:1 with
DB source_keys; the DB is the truth source for enable/quota, registry is the "key → implementation" lookup.
"""
from __future__ import annotations

from .base import Source
from .reddit_source import RedditSource
from .product_hunt_source import ProductHuntSource
from .xiaohongshu_source import XiaohongshuSource

# source_key -> adapter class (mirrors the sources table's source_key / adapter_class)
SOURCE_REGISTRY: dict[str, type[Source]] = {
    RedditSource.name: RedditSource,             # 'reddit'        -> RedditSource
    ProductHuntSource.name: ProductHuntSource,   # 'product_hunt'  -> ProductHuntSource
    XiaohongshuSource.name: XiaohongshuSource,   # 'xiaohongshu'   -> XiaohongshuSource (stub)
}


def get_source(source_key: str, cfg: dict) -> Source:
    """Instantiate the matching data source by source_key. Raises KeyError (with available list) if unregistered."""
    try:
        cls = SOURCE_REGISTRY[source_key]
    except KeyError:
        raise KeyError(
            f"Unregistered data source: {source_key!r}. Registered: {available_sources()}"
        ) from None
    return cls(cfg)


def available_sources() -> list[str]:
    return sorted(SOURCE_REGISTRY)


def build_sources(cfg: dict, source_keys) -> list[Source]:
    """Batch-instantiate from a given source_key list (typically from sources where enabled=TRUE)."""
    return [get_source(k, cfg) for k in source_keys]
