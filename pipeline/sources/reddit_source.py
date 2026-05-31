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

# ---- Comment-page parsing (Anna 2026-05-31, comments_summary wiring) ----
#
# old.reddit's comment thread is a forest of <div data-type="comment" ...> elements. For Top-20
# enrichment we only need the top N by score from the first level, plus is_op detection. Body
# lives in a nested <div class="md"> right after the comment opening.
_COMMENT_OPEN_RE = re.compile(
    r'<div([^>]*?data-fullname="(t1_[a-z0-9]+)"[^>]*?data-type="comment"[^>]*)>',
    re.IGNORECASE,
)
# Score: the span's title attr is the canonical integer (visible "1 point" is rounded text).
_COMMENT_SCORE_RE = re.compile(r'<span class="score unvoted"[^>]*?title="(-?\d+)"', re.IGNORECASE)
# Body: first <div class="md ..."> after the comment opening. The body content is markdown-rendered HTML.
_COMMENT_BODY_RE = re.compile(r'<div class="md[^"]*"[^>]*>(.+?)</div>\s*</form>', re.DOTALL | re.IGNORECASE)
_HTML_TAG_RE = re.compile(r'<[^>]+>')

# Authors we never want to surface — these are bot / mod posts that drown out real discussion.
_COMMENT_AUTHOR_BLOCKLIST = frozenset({"AutoModerator", "[deleted]", "[removed]"})

# Bodies that are deleted / removed — Reddit replaces the markdown with these literal strings.
_DEAD_BODY_STRINGS = ("[deleted]", "[removed]")

_BODY_MAX_CHARS = 800  # Per Anna's 2026-05-31 sizing — gives System ② enough raw material.


def _strip_html_to_text(s: str) -> str:
    """Strip HTML tags and decode minimal entities (Reddit-relevant ones).

    Reddit's md→HTML output produces `<p>`, `<strong>`, `<em>`, `<a>`, `<blockquote>`, and
    occasionally `<code>`. We don't render HTML to the user — we want plain text for AI prompts
    and for the report's "💬 hot comments" preview. Whitespace normalized to single spaces.
    """
    # Replace common block-level closers with spaces so paragraph boundaries don't merge into
    # one long blob (`</p><p>` would otherwise become contiguous).
    s = re.sub(r"</(p|li|blockquote|h[1-6])>", " ", s, flags=re.IGNORECASE)
    s = _HTML_TAG_RE.sub("", s)
    # Decode the handful of entities Reddit emits.
    s = (s.replace("&amp;", "&")
           .replace("&#32;", " ")
           .replace("&lt;", "<")
           .replace("&gt;", ">")
           .replace("&quot;", '"')
           .replace("&#39;", "'")
           .replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", s).strip()


def parse_old_reddit_comments(html: str, op_author: str | None = None,
                              max_comments: int = 10) -> list[dict]:
    """Parse one old.reddit comment page → top-N comments (sorted by score desc).

    Filters out:
    - AutoModerator and [deleted] / [removed] author posts (bot noise + dead content)
    - Empty bodies / bodies that are literally "[deleted]" or "[removed]"

    Each comment dict: {id, author, score, body, is_op, replies}
    - body is plain text (HTML stripped), capped at _BODY_MAX_CHARS (800).
    - is_op = author equals the post's OP author (op_author kwarg) — useful for filtering follow-up
      replies in System ② drafting, where community responses matter more than OP's own threads.

    Returns: at most max_comments items, sorted by score descending. Empty list on no matches.
    """
    # Collect every comment opening + the slice of HTML that "belongs to" each one (= up to the
    # next comment opening or end of html). The slice is where we look for score + body.
    matches = list(_COMMENT_OPEN_RE.finditer(html))
    out: list[dict] = []
    for i, m in enumerate(matches):
        tag_body, fullname = m.groups()
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        slice_ = html[m.end():next_start]

        author_m = re.search(r'data-author="([^"]*)"', tag_body)
        author = author_m.group(1) if author_m else ""
        if author in _COMMENT_AUTHOR_BLOCKLIST:
            continue

        score_m = _COMMENT_SCORE_RE.search(slice_)
        score = int(score_m.group(1)) if score_m else 0

        body_m = _COMMENT_BODY_RE.search(slice_)
        body_raw = body_m.group(1) if body_m else ""
        body = _strip_html_to_text(body_raw)
        if not body or body in _DEAD_BODY_STRINGS:
            continue
        if len(body) > _BODY_MAX_CHARS:
            body = body[:_BODY_MAX_CHARS].rstrip() + "…(truncated)"

        replies_m = re.search(r'data-replies="(\d+)"', tag_body)
        replies = int(replies_m.group(1)) if replies_m else 0

        out.append({
            "id": fullname[3:],          # strip "t1_" prefix
            "fullname": fullname,
            "author": author,
            "score": score,
            "body": body,
            "is_op": bool(op_author) and author == op_author,
            "replies": replies,
        })

    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:max_comments]


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

    # ---- Comment enrichment (Anna 2026-05-31, called by runner after Top-N selection) ----
    def fetch_post_comments(self, permalink: str, op_author: str | None = None,
                            max_comments: int = 10) -> list[dict]:
        """Fetch one post's comments page from old.reddit and return top-N by score.

        permalink: from HotItem.source_native["permalink"] (e.g. "/r/SaaS/comments/abc/slug/")
        op_author: from HotItem.author — used to flag is_op replies.
        Failure (network / parse) → empty list (don't blow up the pipeline; comments are optional).
        """
        if not permalink:
            return []
        # Only old_html mode currently knows the bypass path; JSON mode would need a different URL.
        if self.mode != "old_html":
            return []
        url = f"{OLD_HTML_BASE}{permalink.rstrip('/')}/"
        headers = {"User-Agent": self._ua()}
        try:
            r = self._request_with_retry("GET", url, headers=headers)
        except Exception as e:
            print(f"[reddit:old_html] comments fetch failed for {permalink}: {e}")
            return []
        return parse_old_reddit_comments(r.text, op_author=op_author, max_comments=max_comments)

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
