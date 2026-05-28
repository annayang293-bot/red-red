"""Xiaohongshu data source — STUB (forward-compat placeholder, not yet implemented).

Why it exists = it proves the plugin architecture closes the loop: the registry already
registers this adapter. Note: the sources table seed currently only contains Reddit + Product Hunt,
**no** Xiaohongshu row has been INSERTed yet.

To actually wire it up: ① fill in fetch() (returning List[HotItem]); ② INSERT one row into
the sources table (source_key='xiaohongshu', adapter_class='XiaohongshuSource'). Pipeline /
scoring / persistence need zero changes.

Implementation path (Richard 2026-05-24 research conclusion): the Xiaohongshu bottleneck is
**the request-signing layer + login wall**, not page parsing — so **Firecrawl is not a fit
for the XHS main path** (it's strong at JS rendering / markdown-ification, doesn't solve signing).
Better candidates: ① a dedicated Apify actor (rednote scraper; validate on the free tier first);
② Browserbase / Computer Use with a logged-in browser + real session, sidestepping the
signing arms race. (Firecrawl is still usable for open overseas-source ingestion, but that
belongs to the reddit / PH adapters, not here.)
"""
from __future__ import annotations

from .base import Source


class XiaohongshuSource(Source):
    name = "xiaohongshu"

    def fetch(self):
        raise NotImplementedError(
            "XiaohongshuSource not yet implemented — forward-compat placeholder. "
            "When wiring it up, produce List[HotItem] here (via Firecrawl / official / 3rd party); "
            "the pipeline does not need to change."
        )
