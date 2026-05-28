"""Data-source plugin package.

Public API:
  from pipeline.sources import get_source, available_sources, build_sources, SOURCE_REGISTRY
"""
from .base import Source
from .registry import (
    SOURCE_REGISTRY,
    get_source,
    available_sources,
    build_sources,
)

__all__ = [
    "Source",
    "SOURCE_REGISTRY",
    "get_source",
    "available_sources",
    "build_sources",
]
