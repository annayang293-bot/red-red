"""Source abstract interface — the unified contract for every data source.

To add a new source: subclass Source, set name (== sources.source_key), implement fetch()
returning List[HotItem] (with raw_metrics / source_native / source-native fields populated;
hot_score / relevance_score are computed by the scoring layer). Then register it in
registry.py and INSERT one row into the sources table — pipeline / scoring / persistence
need zero changes.
"""
from __future__ import annotations

import abc


class Source(abc.ABC):
    name: str  # "reddit" | "product_hunt" | "xiaohongshu" — must == sources.source_key

    def __init__(self, cfg: dict):
        self.cfg = cfg

    @abc.abstractmethod
    def fetch(self):
        """Return list[HotItem] (unscored). Implementations only fetch the minimum required fields."""
        raise NotImplementedError
