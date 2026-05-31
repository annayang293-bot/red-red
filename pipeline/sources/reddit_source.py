"""Reddit source — three fetch modes (config.reddit.auth_mode):

- "old_html" (default since 2026-05-31, Anna 2026-05-31): scrapes https://old.reddit.com/r/<sub>/hot/
  HTML pages. Reddit's 2025-11 Responsible Builder Policy aggressively 403s the JSON API for
  non-residential IPs (datacenter / Tor / public proxies), but **HTML pages still return 200 with
  vote data embedded as `data-*` attributes**. This bypass requires no OAuth, no paid proxy, no
  third-party service. Parsed via stdlib regex on `data-fullname` / `data-score` /
  `data-comments-count` / `data-author` / `data-permalink` / `data-timestamp`.
- "public" (legacy): hits https://www.reddit.com/r/<sub>/<listing>.json. Read-only public JSON, no
  app needed. Currently broken under Reddit's anti-bot — keep as a fallback for when it comes back.
- "oauth" (more robust): application-only OAuth (client_credentials), needs REDDIT_CLIENT_ID /
  REDDIT_CLIENT_SECRET in .env. Untested under current policy; reserved for Junxi's app approval.

Robustness across all modes: UA validation; exponential backoff on rate-limit / transient errors +
honor Retry-After; failed subs go into failed_subs rather than being swallowed silently.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone

import requests

from .base import Source
from ..schema import HotItem, make_id, canonical_url, clip_snippet, now_iso, to_iso

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
OAUTH_BASE = "https://oauth.reddit.com"
PUBLIC_BASE = "https://www.reddit.com"
OLD_HTML_BASE = "https://old.reddit.com"
DEFAULT_UA = "python:system1-app:v0.1 (by /u/CHANGE_ME)"
_BAD_UA_TOKENS = ("CHANGE_ME", "yourname", "<realuser>", "<user>")
_MAX_RETRIES = 3

# ---- old.reddit.com HTML parsing helpers (Anna 2026-05-31) ----
#
# old.reddit.com renders each post as a <div class="...thing..." data-*="..."> block. The data
# attributes carry the structured fields we need — far more reliable than scraping visible text:
#   data-fullname        → "t3_<id>"   (the canonical Reddit post id, e.g. t3_1tssw2a)
#   data-author          → username
#   data-permalink       → "/r/sub/comments/<id>/<slug>/"
#   data-score           → integer (net upvotes)
#   data-comments-count  → integer
#   data-timestamp       → milliseconds since epoch
#   class="...stickied..." → marks pinned posts (megathreads / mod posts) that should be filtered
#
# Title comes from the next <a class="title">...</a> following the div opening. Flair (if set)
# comes from <span class="linkflairlabel" title="...">.
#
# Regex over HTML is brittle in general, but old.reddit's markup has been stable since ~2014 and
# is preserved specifically for legacy moderation tools — they're unlikely to break it. The
# data-* attribute approach also doesn't depend on the visible text layout, which is the part
# most often re-skinned.

# Match the entire opening <div ...> tag once so we can scan it for `stickied` (which appears
# in the class attribute BEFORE data-fullname, so a trailing-only capture would miss it).
_POST_RE = re.compile(
    r'<div([^>]*?data-fullname="(t3_[a-z0-9]+)"[^>]*)>',              # group 1: full tag body, group 2: fullname
    re.IGNORECASE,
)
_ATTR_AUTHOR_RE = re.compile(r'data-author="([^"]*)"', re.IGNORECASE)
_ATTR_PERMALINK_RE = re.compile(r'data-permalink="([^"]*)"', re.IGNORECASE)
_ATTR_SCORE_RE = re.compile(r'data-score="(-?\d+)"', re.IGNORECASE)
_ATTR_COMMENTS_RE = re.compile(r'data-comments-count="(\d+)"', re.IGNORECASE)
_ATTR_TIMESTAMP_RE = re.compile(r'data-timestamp="(\d+)"', re.IGNORECASE)
_ATTR_SUBREDDIT_RE = re.compile(r'data-subreddit="([^"]*)"', re.IGNORECASE)
_TITLE_RE = re.compile(r'<a[^>]+class="title[^"]*"[^>]*>([^<]+)</a>')
_FLAIR_RE = re.compile(r'<span[^>]+class="[^"]*linkflairlabel[^"]*"[^>]+title="([^"]*)"')


def parse_old_reddit_html(html: str) -> list[dict]:
    """Parse one `old.reddit.com/r/<sub>/...` page into a list of post dicts.

    Returns: [{id, title, author, permalink, score, comments, timestamp_ms, subreddit,
               is_stickied, flair}] in the page's natural order.
    Empty input or no matches → empty list (caller decides how to handle).
    """
    posts: list[dict] = []
    for m in _POST_RE.finditer(html):
        tag_body, fullname = m.groups()
        # Scan the whole opening tag once for each attribute (instead of a giant lookahead chain
        # whose group order had to be remembered). Missing required field → skip the post.
        a = _ATTR_AUTHOR_RE.search(tag_body)
        p = _ATTR_PERMALINK_RE.search(tag_body)
        s = _ATTR_SCORE_RE.search(tag_body)
        c = _ATTR_COMMENTS_RE.search(tag_body)
        t = _ATTR_TIMESTAMP_RE.search(tag_body)
        sr = _ATTR_SUBREDDIT_RE.search(tag_body)
        if not (a and p and s and c and t and sr):
            continue
        # `stickied` lives in the class= attribute, BEFORE data-fullname — so we need to scan the
        # full tag body, not just what's after data-fullname.
        is_stickied = "stickied" in tag_body
        # Search the next ~8KB for the title (covers any reasonable thumbnail/UI block in between).
        title_match = _TITLE_RE.search(html, m.end(), m.end() + 8000)
        title = title_match.group(1).strip() if title_match else ""
        # Flair is optional; same lookahead window.
        flair_match = _FLAIR_RE.search(html, m.end(), m.end() + 8000)
        flair = flair_match.group(1).strip() if flair_match else ""
        posts.append({
            "id": fullname[3:],                                       # strip "t3_" prefix → bare id
            "fullname": fullname,
            "title": title,
            "author": a.group(1),
            "permalink": p.group(1),
            "score": int(s.group(1)),
            "comments": int(c.group(1)),
            "timestamp_ms": int(t.group(1)),
            "subreddit": sr.group(1),
            "is_stickied": is_stickied,
            "flair": flair,
        })
    return posts


class RedditSource(Source):
    name = "reddit"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.rc = cfg["reddit"]
        self.mode = (self.rc.get("auth_mode") or "old_html").lower()
        self._token = None
        self._token_exp = 0.0
        self.failed_subs: list[str] = []

    # ---- UA / auth ----
    def _ua(self) -> str:
        return os.environ.get("REDDIT_USER_AGENT", DEFAULT_UA)

    def _validate_ua(self):
        ua = self._ua()
        if any(tok in ua for tok in _BAD_UA_TOKENS) or "(by /u/" not in ua:
            raise RuntimeError(
                "Reddit User-Agent is not compliant. Set REDDIT_USER_AGENT in .env, "
                "format: 'python:system1-app:v0.1 (by /u/your-real-reddit-username)' "
                f"(current: {ua!r})"
            )

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

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        cid = os.environ.get("REDDIT_CLIENT_ID")
        csec = os.environ.get("REDDIT_CLIENT_SECRET")
        if not cid or not csec:
            raise RuntimeError(
                "oauth mode is missing REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET. "
                "If you can't create an app right now, set reddit.auth_mode: old_html to get going."
            )
        resp = self._request_with_retry(
            "POST", TOKEN_URL,
            auth=(cid, csec),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self._ua()},
        )
        j = resp.json()
        self._token = j["access_token"]
        self._token_exp = time.time() + j.get("expires_in", 3600)
        return self._token

    # ---- fetch dispatch ----
    def fetch(self):
        self._validate_ua()
        self.failed_subs = []
        if self.mode == "old_html":
            return self._fetch_old_html()
        return self._fetch_json()

    # ---- mode: old_html (default since 2026-05-31, Reddit JSON API blocked) ----
    def _fetch_old_html(self) -> list[HotItem]:
        """Scrape old.reddit.com/r/<sub>/<listing>/ HTML for each subreddit.

        The data attributes give us every field we need for the pipeline (score, comments,
        timestamp, permalink). Stickied posts and flair-blacklisted posts are filtered the same
        way as the JSON path.
        """
        listing = self.rc.get("listing", "hot")
        # old.reddit.com paginates at 25 by default; ask for the same upper bound the JSON path used.
        limit = min(int(self.rc.get("fetch_limit_per_sub", 60)), 100)
        excluded_flairs = [f.lower() for f in self.rc.get("excluded_flairs", [])]
        headers = {"User-Agent": self._ua()}
        items: list[HotItem] = []
        for sub in self.rc.get("subreddits", []):
            url = f"{OLD_HTML_BASE}/r/{sub}/{listing}/"
            params = {"limit": limit}
            if listing == "top":
                params["t"] = self.rc.get("time_filter", "day")
            try:
                r = self._request_with_retry("GET", url, headers=headers, params=params)
            except Exception as e:
                self.failed_subs.append(sub)
                print(f"[reddit:old_html] r/{sub} fetch failed (after retries): {e}")
                continue
            posts = parse_old_reddit_html(r.text)
            if not posts:
                # Page returned 200 but no posts parsed — likely a banned/private notice page
                # rather than a true subreddit listing. Surface it so the operator notices.
                self.failed_subs.append(sub)
                print(f"[reddit:old_html] r/{sub} parsed 0 posts (banned/private/empty?)")
                continue
            for p in posts:
                if p["is_stickied"]:
                    continue
                fl = (p["flair"] or "").lower()
                if fl and any(bad in fl for bad in excluded_flairs):
                    continue
                native_id = p["id"]
                permalink = p["permalink"]
                link = f"https://www.reddit.com{permalink}" if permalink else ""
                if not native_id or not link:
                    print(f"[reddit:old_html] skipping post missing id/url: {p['title'][:50]!r}")
                    continue
                pub = datetime.fromtimestamp(p["timestamp_ms"] / 1000, tz=timezone.utc)
                items.append(HotItem(
                    id=make_id(self.name, native_id),
                    dedup_key=canonical_url(link),
                    title=p["title"],
                    source=self.name,
                    source_native_id=native_id,
                    url=link,
                    author=p["author"] or None,
                    published_at=to_iso(pub),
                    captured_at=now_iso(),
                    lang="en",  # Same V1 simplification as the JSON path.
                    media_type="text",  # old.reddit HTML doesn't surface media type cleanly; assume text.
                    raw_metrics={
                        "likes": p["score"],
                        "upvotes": p["score"],  # old.reddit only exposes net score; same value used for both
                        "comments": p["comments"],
                        "saves": 0,  # Not exposed via HTML. Was a proxy field in JSON path; drop here.
                    },
                    source_native={
                        "subreddit": p["subreddit"],
                        "permalink": permalink,
                        "fetch_mode": "old_html",
                        "link_flair_text": p["flair"] or None,
                    },
                    tags=[t for t in [p["subreddit"], p["flair"]] if t],
                    raw_snippet="",  # old.reddit's body excerpt is tricky to parse cleanly; leave empty.
                ))
        return items

    # ---- mode: public / oauth (legacy JSON path) ----
    def _listing_path_json(self, sub: str) -> str:
        listing = self.rc.get("listing", "hot")
        if self.mode == "oauth":
            return f"{OAUTH_BASE}/r/{sub}/{listing}"
        return f"{PUBLIC_BASE}/r/{sub}/{listing}.json"

    def _fetch_json(self) -> list[HotItem]:
        if self.mode == "oauth":
            token = self._get_token()
            headers = {"Authorization": f"Bearer {token}", "User-Agent": self._ua()}
        else:
            headers = {"User-Agent": self._ua()}  # public: no token/credentials needed
        limit = int(self.rc.get("fetch_limit_per_sub", 60))
        proxy_field = self.rc.get("saveshare_proxy_field", "num_crossposts")
        items: list[HotItem] = []
        for sub in self.rc.get("subreddits", []):
            params = {"limit": min(limit, 100), "raw_json": 1}
            if self.rc.get("listing") == "top":
                params["t"] = self.rc.get("time_filter", "day")
            try:
                r = self._request_with_retry(
                    "GET", self._listing_path_json(sub), headers=headers, params=params)
            except Exception as e:
                self.failed_subs.append(sub)
                print(f"[reddit:{self.mode}] r/{sub} fetch failed (after retries): {e}")
                continue
            excluded_flairs = [f.lower() for f in self.rc.get("excluded_flairs", [])]
            for child in r.json().get("data", {}).get("children", []):
                d = child.get("data", {})
                if d.get("stickied"):
                    continue
                _flair = (d.get("link_flair_text") or "").lower()
                if _flair and any(bad in _flair for bad in excluded_flairs):
                    continue  # Flair blacklist: filter out meme/joke etc.
                native_id = d.get("id", "")
                permalink = d.get("permalink", "")
                link = f"https://www.reddit.com{permalink}" if permalink else d.get("url", "")
                # Skip records missing key primary fields to avoid empty source_native_id / empty url
                # tripping constraints or causing hash collisions.
                if not native_id or not link:
                    print(f"[reddit:{self.mode}] skipping post missing id/url: {(d.get('title') or '')[:50]!r}")
                    continue
                created = d.get("created_utc")
                pub = (datetime.fromtimestamp(created, tz=timezone.utc)
                       if created else None)
                is_video = bool(d.get("is_video")) or d.get("post_hint") == "hosted:video"
                has_img = d.get("post_hint") == "image" or bool(d.get("preview"))
                media_type = "video" if is_video else ("image" if has_img else "text")
                items.append(HotItem(
                    id=make_id(self.name, native_id),
                    dedup_key=canonical_url(link),
                    title=d.get("title", ""),
                    source=self.name,
                    source_native_id=native_id,
                    url=link,
                    author=d.get("author"),
                    published_at=to_iso(pub),
                    captured_at=now_iso(),
                    lang="en",
                    media_type=media_type,
                    raw_metrics={
                        "likes": d.get("score", 0),
                        "upvotes": d.get("ups", 0),
                        "comments": d.get("num_comments", 0),
                        "saves": d.get(proxy_field, 0) or 0,
                    },
                    source_native={
                        "subreddit": d.get("subreddit"),
                        "permalink": permalink,
                        "upvote_ratio": d.get("upvote_ratio"),
                        "num_crossposts": d.get("num_crossposts"),
                        "over_18": d.get("over_18"),
                        "link_flair_text": d.get("link_flair_text"),
                    },
                    tags=[t for t in [d.get("subreddit"), d.get("link_flair_text")] if t],
                    raw_snippet=clip_snippet(d.get("selftext") or ""),
                ))
        return items
