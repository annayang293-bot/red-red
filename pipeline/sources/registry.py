"""数据源注册表 —— 把 sources 表的 source_key/adapter_class 映射到 Python 类。

这是「可插拔」的核心。加新源(如小红书)三步,其余代码完全不动:
  1) 写一个 XxxSource(Source) 实现 fetch()(产出 List[HotItem])
  2) 在下面 SOURCE_REGISTRY 注册一行
  3) 往 sources 表 INSERT 一行 (source_key, adapter_class='XxxSource', enabled, quota_top20)

主线运行时:从 sources 表读 enabled=TRUE 的 source_key 列表 → get_source() 实例化 →
逐个 fetch() 汇总。注册表的 key 与 DB 的 source_key 一一对应,DB 是 enable/配额的
真相源,registry 是「key → 实现」的查找表。
"""
from __future__ import annotations

from .base import Source
from .reddit_source import RedditSource
from .product_hunt_source import ProductHuntSource
from .xiaohongshu_source import XiaohongshuSource

# source_key -> adapter class(与 sources 表 source_key / adapter_class 对应)
SOURCE_REGISTRY: dict[str, type[Source]] = {
    RedditSource.name: RedditSource,             # 'reddit'        -> RedditSource
    ProductHuntSource.name: ProductHuntSource,   # 'product_hunt'  -> ProductHuntSource
    XiaohongshuSource.name: XiaohongshuSource,   # 'xiaohongshu'   -> XiaohongshuSource (stub)
}


def get_source(source_key: str, cfg: dict) -> Source:
    """按 source_key 实例化对应数据源。未注册则抛 KeyError(带可用列表)。"""
    try:
        cls = SOURCE_REGISTRY[source_key]
    except KeyError:
        raise KeyError(
            f"未注册的数据源: {source_key!r}。已注册: {available_sources()}"
        ) from None
    return cls(cfg)


def available_sources() -> list[str]:
    return sorted(SOURCE_REGISTRY)


def build_sources(cfg: dict, source_keys) -> list[Source]:
    """按给定 source_key 列表(通常来自 sources 表 enabled=TRUE)批量实例化。"""
    return [get_source(k, cfg) for k in source_keys]
