"""Source 抽象接口 —— 所有数据源的统一契约。

新源只需:继承 Source、设 name(== sources.source_key)、实现 fetch() 返回
List[HotItem](已填 raw_metrics/source_native/原始字段;hot_score/relevance_score
由 scoring 层统一计算)。然后在 registry.py 注册一行 + sources 表 INSERT 一行即可,
主线/打分/入库零改动。
"""
from __future__ import annotations

import abc


class Source(abc.ABC):
    name: str  # "reddit" | "product_hunt" | "xiaohongshu" —— 必须 == sources.source_key

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @abc.abstractmethod
    def fetch(self):
        """返回 list[HotItem](未打分)。实现内只取最小必要字段。"""
        raise NotImplementedError
