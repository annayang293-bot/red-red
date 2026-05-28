"""Product Hunt source — two fetch modes (config.product_hunt.auth_mode):

- "rss" (default, zero credentials): reads the public Atom feed at https://www.producthunt.com/feed
  No app / no token required. Limitation: the public feed **doesn't include votes or comment counts** →
  raw_metrics is all 0 (it gets sorted via relevance + recency instead).
- "token" (full data): GraphQL v2, client_credentials developer token, with votesCount / commentsCount.
  Needs PH_CLIENT_ID / PH_CLIENT_SECRET in .env.

No new dependencies (stdlib xml). GraphQL complexity is kept tight: token mode requests
only the necessary fields, first<=20.
"""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from .base import Source
from ..schema import HotItem, make_id, canonical_url, clip_snippet, now_iso, to_iso

ATOM_NS = "{http://www.w3.org/2005/Atom}"
TOKEN_URL = "https://api.producthunt.com/v2/oauth/token"
GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"
DEFAULT_UA = "system1-app/0.1 (internal learning; +https://example.local)"
_MAX_RETRIES = 3
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", s)).strip()


def _parse_dt(s: str | None):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class ProductHuntSource(Source):
    name = "product_hunt"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.pc = cfg.get("product_hunt", {}) or {}
        self.mode = (self.pc.get("auth_mode") or "rss").lower()
        self.failed = False

    def _ua(self) -> str:
        return os.environ.get("PH_USER_AGENT", DEFAULT_UA)

    def _request_with_retry(self, method, url, **kw):
        last = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.request(method, url, timeout=25, **kw)
            except requests.RequestException as e:
                last = e
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = resp.headers.get("Retry-After")
                time.sleep(float(wait) if wait else 2 ** attempt)
                last = requests.HTTPError(f"{resp.status_code} {url}")
                continue
            resp.raise_for_status()
            return resp
        raise last if last else RuntimeError(f"Request failed: {url}")

    # ---- fetch ----
    def fetch(self):
        self.failed = False
        try:
            if self.mode == "token":
                return self._fetch_token()
            return self._fetch_rss()
        except Exception as e:
            self.failed = True
            print(f"[product_hunt:{self.mode}] fetch failed (after retries): {e}")
            return []

    # ---- rss (default, no token) ----
    def _fetch_rss(self):
        url = self.pc.get("rss_url", "https://www.producthunt.com/feed")
        limit = int(self.pc.get("fetch_limit", 40))
        r = self._request_with_retry(
            "GET", url, headers={"User-Agent": self._ua()})
        root = ET.fromstring(r.content)
        items: list[HotItem] = []
        for entry in root.findall(f"{ATOM_NS}entry")[:limit]:
            def t(tag):
                el = entry.find(f"{ATOM_NS}{tag}")
                return el.text if el is not None else None
            native_id = (t("id") or "").strip()
            link_el = entry.find(f"{ATOM_NS}link")
            link = link_el.get("href") if link_el is not None else (t("id") or "")
            author_el = entry.find(f"{ATOM_NS}author/{ATOM_NS}name")
            author = author_el.text if author_el is not None else None
            content_el = entry.find(f"{ATOM_NS}content")
            summary = (content_el.text if content_el is not None else None) or t("summary")
            pub = _parse_dt(t("published") or t("updated"))
            cats = [c.get("term") for c in entry.findall(f"{ATOM_NS}category")
                    if c.get("term")]
            if not native_id and not link:
                continue
            items.append(HotItem(
                id=make_id(self.name, native_id or link),
                dedup_key=canonical_url(link),
                title=(t("title") or "").strip(),
                source=self.name,
                source_native_id=(native_id or link),
                url=link,
                author=author,
                published_at=to_iso(pub),
                captured_at=now_iso(),
                lang="en",
                media_type="text",
                # Public RSS has no engagement data → all zeros (known limitation; token mode fills these in)
                raw_metrics={"likes": 0, "comments": 0, "saves": 0, "upvotes": 0},
                source_native={"feed_id": native_id, "categories": cats,
                               "ph_mode": "rss"},
                tags=cats,
                raw_snippet=clip_snippet(_strip_html(summary)),
            ))
        return items

    # ---- token (GraphQL, full data) ----
    def _get_token(self) -> str:
        cid = os.environ.get("PH_CLIENT_ID")
        csec = os.environ.get("PH_CLIENT_SECRET")
        if not cid or not csec:
            raise RuntimeError(
                "token mode is missing PH_CLIENT_ID / PH_CLIENT_SECRET. "
                "If you don't have a token yet, set product_hunt.auth_mode: rss (default).")
        resp = self._request_with_retry(
            "POST", TOKEN_URL,
            json={"client_id": cid, "client_secret": csec,
                  "grant_type": "client_credentials"},
            headers={"Content-Type": "application/json", "User-Agent": self._ua()})
        return resp.json()["access_token"]

    def _fetch_token(self):
        token = self._get_token()
        first = min(int(self.pc.get("graphql_first", 20)), 20)
        query = (
            "{ posts(order: VOTES, first: %d) { edges { node { "
            "id name tagline url votesCount commentsCount createdAt featuredAt "
            "topics(first: 3) { edges { node { name } } } } } } }" % first)
        r = self._request_with_retry(
            "POST", GRAPHQL_URL, json={"query": query},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json",
                     "User-Agent": self._ua()})
        # GraphQL still returns HTTP 200 even when the token is invalid, scope is wrong, or the
        # query is rejected — it just carries `errors`. Must check explicitly, otherwise failures
        # get disguised as "no data today" (silent failure).
        payload = r.json()
        if payload.get("errors"):
            raise RuntimeError(f"Product Hunt GraphQL returned errors: {payload['errors']}")
        posts = (payload.get("data") or {}).get("posts")
        if posts is None:
            raise RuntimeError(
                f"Product Hunt GraphQL response missing data.posts: {str(payload)[:200]}")
        data = posts.get("edges", [])
        items: list[HotItem] = []
        for edge in data:
            n = edge.get("node", {})
            nid = str(n.get("id", "") or "")
            url = n.get("url", "") or ""
            if not nid or not url:
                print("[product_hunt:token] skipping entry missing id/url")
                continue
            topics = [tp["node"]["name"] for tp in
                      n.get("topics", {}).get("edges", []) if tp.get("node")]
            pub = _parse_dt(n.get("createdAt"))
            items.append(HotItem(
                id=make_id(self.name, nid),
                dedup_key=canonical_url(url),
                title=n.get("name", ""),
                source=self.name,
                source_native_id=nid,
                url=url,
                author=None,
                published_at=to_iso(pub),
                captured_at=now_iso(),
                lang="en",
                media_type="text",
                raw_metrics={"likes": n.get("votesCount", 0),
                             "upvotes": n.get("votesCount", 0),
                             "comments": n.get("commentsCount", 0),
                             "saves": 0},
                source_native={"ph_topics": topics, "tagline": n.get("tagline"),
                               "featuredAt": n.get("featuredAt"),
                               "ph_mode": "token"},
                tags=topics,
                raw_snippet=clip_snippet(n.get("tagline") or ""),
            ))
        return items
