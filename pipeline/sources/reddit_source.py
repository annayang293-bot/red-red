"""Reddit source — four fetch modes (config.reddit.auth_mode):

- "apify" (default since 2026-05-31, Anna): uses the Apify-hosted `harshmaur/reddit-scraper`
  actor. Apify's residential proxy pool sidesteps Reddit's 2025-11 IP-class block. Split
  architecture (Anna 2026-05-31): one Apify run for the listing call (`startUrls = [N sub URLs]`,
  `crawlCommentsPerPost: false`) returns ~30 posts per sub with no comments attached; later, after
  the pipeline picks Top-N, a second Apify run fetches threaded comments for just those
  permalinks (`startUrls = [Top-N URLs]`, `crawlCommentsPerPost: true`). `fetch_comments_for_urls`
  exposes the batch interface; `runner._enrich_top_with_comments` detects and routes through it
  instead of the per-post loop. Requires `APIFY_TOKEN` in env (`.env` locally, Vercel/GH secrets
  in CI). See `docs/APIFY_RESEARCH.md` for actor choice + cost analysis.
- "old_html": scrapes https://old.reddit.com/r/<sub>/hot/ HTML pages. Was the default
  2026-05-25 → 2026-05-31 after Reddit JSON started 403'ing datacenter IPs; now demoted to
  fallback because GitHub-hosted runners get the same 403 (verified run 52, 2026-05-31).
- "public" (legacy): hits https://www.reddit.com/r/<sub>/<listing>.json. Read-only public JSON.
- "oauth" (legacy): application-only OAuth (client_credentials). Reserved if Reddit ever brings
  back IP-class-agnostic read-only access via OAuth.

Robustness across all modes: failed subs go into `failed_subs` rather than being swallowed
silently. The Apify path batches all subs into a single Apify run (`startUrls = [N URLs]`); if
that whole run fails, every requested sub lands in `failed_subs`. Subs that the run silently
omits (private / banned / typo) also get marked failed by post-processing.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from .base import Source
from ..schema import HotItem, make_id, canonical_url, clip_snippet, now_iso, to_iso

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
OAUTH_BASE = "https://oauth.reddit.com"
PUBLIC_BASE = "https://www.reddit.com"
OLD_HTML_BASE = "https://old.reddit.com"
APIFY_BASE = "https://api.apify.com"
APIFY_ACTOR = "harshmaur~reddit-scraper"   # see docs/APIFY_RESEARCH.md for the pivot from fatihtahta
DEFAULT_UA = "python:system1-app:v0.1 (by /u/CHANGE_ME)"
_BAD_UA_TOKENS = ("CHANGE_ME", "yourname", "<realuser>", "<user>")
_MAX_RETRIES = 3
_APIFY_RUN_TIMEOUT_S = 180         # Per-sub timeout. Probed runs took 40-90s; 3x headroom.
_APIFY_POLL_INTERVAL_S = 5

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
        self.mode = (self.rc.get("auth_mode") or "apify").lower()
        self._token = None
        self._token_exp = 0.0
        self.failed_subs: list[str] = []
        # Apify path: comments are fetched inline alongside the listing. _enrich_top_with_comments
        # in runner.py later calls fetch_post_comments(permalink) per Top-N item — we serve those
        # calls from this cache instead of going back over the network. Keyed by canonical
        # permalink (e.g. "/r/OpenAI/comments/abc/slug/"). Repopulated on every fetch().
        self._apify_comments_by_permalink: dict[str, list[dict]] = {}

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
        self.failed_subs = []
        if self.mode == "apify":
            # Apify is hosted; it owns the UA / proxy / rate-limit story. We don't validate the
            # local REDDIT_USER_AGENT (irrelevant — Apify uses its own) and we don't retry
            # individual HTTP calls (the actor's runtime already does that internally).
            return self._fetch_apify()
        # Legacy paths still need a compliant local UA.
        self._validate_ua()
        if self.mode == "old_html":
            return self._fetch_old_html()
        return self._fetch_json()

    # ---- mode: apify (default since 2026-05-31, Anna; see docs/APIFY_RESEARCH.md) ----
    def _apify_token(self) -> str:
        tok = os.environ.get("APIFY_TOKEN")
        if not tok:
            raise RuntimeError(
                "APIFY_TOKEN missing. Set it in .env locally and in Vercel + GitHub Actions "
                "secrets for prod. If you actually want the deprecated old.reddit fallback for "
                "a one-off run, pass reddit.auth_mode: old_html in cfg."
            )
        return tok

    def _apify_request(self, method: str, path: str, *, body=None, token: str | None = None):
        """Thin wrapper. We do NOT retry on Apify 429 / 5xx — Apify hosts the actor and runs its own
        rate-limit / retry internally. Adding our own sleep on top would double-bill and confuse
        the failure log. We only handle network-level connection errors with a single retry."""
        url = f"{APIFY_BASE}{path}"
        headers = {"Authorization": f"Bearer {token or self._apify_token()}"}
        kwargs = {"timeout": 60, "headers": headers}
        if body is not None:
            headers["Content-Type"] = "application/json"
            kwargs["data"] = json.dumps(body).encode("utf-8")
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.RequestException:
            # One retry for transient connection errors only — Apify itself is a stable service.
            time.sleep(1.0)
            resp = requests.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _apify_run(self, label: str, input_payload: dict, *, token: str) -> dict | None:
        """Generic actor-run helper: POST /runs → poll → GET /datasets/<id>/items.

        Returns {run_id, items, usage_usd} on SUCCEEDED, or None on any failure (the caller logs
        the label so we can tell which call site failed).
        """
        try:
            start = self._apify_request(
                "POST", f"/v2/acts/{quote(APIFY_ACTOR, safe='~')}/runs",
                body=input_payload, token=token,
            )
        except requests.HTTPError as e:
            sc = getattr(e.response, "status_code", "?")
            body = ""
            try:
                body = (e.response.text or "")[:300]
            except Exception:
                pass
            print(f"[reddit:apify] {label} start failed: HTTP {sc} {body}")
            return None
        run_id = start.get("data", {}).get("id")
        if not run_id:
            print(f"[reddit:apify] {label} start returned no run_id: {start}")
            return None
        deadline = time.time() + _APIFY_RUN_TIMEOUT_S
        final = None
        while time.time() < deadline:
            try:
                resp = self._apify_request(
                    "GET", f"/v2/acts/{quote(APIFY_ACTOR, safe='~')}/runs/{run_id}",
                    token=token,
                )
            except requests.HTTPError as e:
                print(f"[reddit:apify] {label} poll failed: {e}")
                time.sleep(_APIFY_POLL_INTERVAL_S)
                continue
            state = resp.get("data", {}).get("status")
            if state in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                final = resp["data"]
                break
            time.sleep(_APIFY_POLL_INTERVAL_S)
        else:
            print(f"[reddit:apify] {label} polling timed out after {_APIFY_RUN_TIMEOUT_S}s (run_id={run_id})")
            return None
        if final.get("status") != "SUCCEEDED":
            print(f"[reddit:apify] {label} actor finished with state={final.get('status')} "
                  f"(run_id={run_id}, usage=${final.get('usageTotalUsd')})")
            return None
        try:
            items = self._apify_request(
                "GET", f"/v2/datasets/{final['defaultDatasetId']}/items",
                token=token,
            )
        except requests.HTTPError as e:
            print(f"[reddit:apify] {label} dataset fetch failed: {e}")
            return None
        return {"run_id": run_id, "items": items, "usage_usd": final.get("usageTotalUsd")}

    def _apify_post_to_hot_item(self, it: dict, excluded_flairs: list[str], sub_fallback: str | None) -> HotItem | None:
        """Map one harshmaur post object → HotItem. Returns None for posts we want to filter
        (stickied / flair-blacklisted / missing id+url).
        """
        if it.get("stickied") or it.get("pinned"):
            return None
        flair = (it.get("flair") or "").lower()
        if flair and any(bad in flair for bad in excluded_flairs):
            return None
        native_id = it.get("parsedId") or it.get("id") or ""
        # harshmaur strips the "t3_" prefix into parsedId; plain `id` may be either form.
        if native_id.startswith("t3_"):
            native_id = native_id[3:]
        if not native_id:
            print(f"[reddit:apify] skipping post missing id: {(it.get('title') or '')[:50]!r}")
            return None
        # `postUrl` is the canonical reddit.com permalink; `url` is the outbound link target for
        # link posts (image / article / video) — we want postUrl for the report card. Permalink
        # is the path-only form harshmaur doesn't supply directly, so derive it from postUrl.
        link = it.get("postUrl") or it.get("url") or ""
        if not link:
            print(f"[reddit:apify] skipping post missing url: {(it.get('title') or '')[:50]!r}")
            return None
        # Derive Reddit path-only permalink (matches the shape parse_old_reddit_html emitted):
        # https://www.reddit.com/r/<sub>/comments/<id>/<slug>/  →  /r/<sub>/comments/<id>/<slug>/
        permalink = ""
        if "reddit.com" in link:
            idx = link.find("/r/")
            if idx != -1:
                permalink = link[idx:]
        pub_iso = it.get("createdAt") or ""
        try:
            pub = datetime.fromisoformat(pub_iso.replace("Z", "+00:00")) if pub_iso else None
        except ValueError:
            pub = None
        score = int(it.get("upVotes") or 0)
        num_comments = int(it.get("commentsCount") or 0)
        # harshmaur exposes `postType` (image / video / link / self / gallery); coarse map.
        ptype = (it.get("postType") or "").lower()
        if ptype == "video":
            media_type = "video"
        elif ptype in ("image", "gallery"):
            media_type = "image"
        else:
            media_type = "text"
        author = it.get("authorName") or None
        sub_name = it.get("communityName") or it.get("parsedCommunityName") or sub_fallback or ""
        if sub_name.startswith("r/"):
            sub_name = sub_name[2:]
        source_native = {
            "subreddit": sub_name,
            "permalink": permalink,
            "fetch_mode": "apify",
            "link_flair_text": it.get("flair"),
            # `comments` is populated later by fetch_comments_for_urls; leave key absent now so the
            # `_enrich_top_with_comments` "if comments: write" check fires on the second pass.
        }
        return HotItem(
            id=make_id(self.name, native_id),
            dedup_key=canonical_url(link),
            title=it.get("title") or "",
            source=self.name,
            source_native_id=native_id,
            url=link,
            author=author,
            published_at=to_iso(pub),
            captured_at=now_iso(),
            lang="en",
            media_type=media_type,
            raw_metrics={
                "likes": score,
                "upvotes": score,
                "comments": num_comments,
                "saves": 0,
            },
            source_native=source_native,
            tags=[t for t in [sub_name, it.get("flair")] if t],
            raw_snippet=clip_snippet(it.get("body") or ""),
        )

    def _fetch_apify(self) -> list[HotItem]:
        """Listing-only fetch: one Apify run with all subreddit URLs, no comments.

        Why split listing from comments (Anna 2026-05-31): comments are only meaningful for the
        top-20 items the report ends up showing — fetching them for every candidate (~180 posts)
        would 3x the per-run cost without surfacing them anywhere. The Top-N comment fetch is
        done later by `fetch_comments_for_urls`, called from runner._enrich_top_with_comments.

        Whole-call failure (Apify down, token revoked, etc.) → all subs land in failed_subs.
        Per-sub failures within a successful run get logged but don't block the rest.
        """
        token = self._apify_token()
        subs = self.rc.get("subreddits") or []
        if not subs:
            return []
        listing = self.rc.get("listing", "hot")
        max_posts = int(self.rc.get("fetch_limit_per_sub", 60))
        excluded_flairs = [f.lower() for f in self.rc.get("excluded_flairs", [])]

        # harshmaur's startUrls accepts a list of subreddit *listing* URLs (e.g. /r/X/hot/).
        # `searchSort` accepts only lowercase values per the actor's input schema
        # ("relevance" / "hot" / "top" / "new" / "comments"). Capitalized "Hot" gets the
        # actor to 400 "invalid-input" before it even starts; verified live in run 53 dispatch.
        start_urls = [{"url": f"https://www.reddit.com/r/{s}/{listing}/"} for s in subs]
        payload = {
            "startUrls": start_urls,
            "searchPosts": False,
            "searchComments": False,
            "searchCommunities": False,
            "searchUsers": False,
            "includeNSFW": False,
            "fastMode": True,
            "crawlCommentsPerPost": False,
            "maxPostsCount": max_posts,
            "maxCommentsPerPost": 0,
            "maxCommentsCount": 0,
            "maxCommunitiesCount": 0,
            "searchSort": listing,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
        result = self._apify_run("listing", payload, token=token)
        if result is None:
            # Whole listing call failed — every sub counts as failed.
            self.failed_subs.extend(subs)
            return []
        items = result["items"]
        print(f"[reddit:apify] listing ok: {len(items)} items "
              f"(run_id={result['run_id']}, usage=${result['usage_usd']})")
        out: list[HotItem] = []
        seen_subs: set[str] = set()
        for raw in items:
            if (raw.get("dataType") or "") != "post":
                continue
            hot = self._apify_post_to_hot_item(raw, excluded_flairs, sub_fallback=None)
            if hot is None:
                continue
            sub = (hot.source_native or {}).get("subreddit") or ""
            if sub:
                seen_subs.add(sub.lower())
            out.append(hot)
        # Any requested sub that didn't show up at all → failed_subs (private / banned /
        # spelt wrong / harshmaur skipped it).
        for s in subs:
            if s.lower() not in seen_subs:
                self.failed_subs.append(s)
        return out

    # ---- Top-N comment enrichment (Apify path) ----
    def fetch_comments_for_urls(self, post_urls: list[str], max_comments: int = 10) -> dict[str, list[dict]]:
        """Batch fetch top-N comments for an arbitrary set of post URLs in a single Apify run.

        Returns a dict keyed by the path-only Reddit permalink (`/r/<sub>/comments/<id>/<slug>/`) →
        list of canonical comment dicts (same shape parse_old_reddit_comments returns). Posts whose
        comments couldn't be fetched simply don't show up in the dict — caller treats absence as
        "no comments." The map is also stashed into `self._apify_comments_by_permalink` so a later
        per-permalink `fetch_post_comments` call serves from memory.
        """
        if not post_urls or max_comments <= 0:
            return {}
        token = self._apify_token()
        payload = {
            "startUrls": [{"url": u} for u in post_urls],
            "searchPosts": False,
            "searchComments": False,
            "searchCommunities": False,
            "searchUsers": False,
            "includeNSFW": False,
            "fastMode": True,
            "crawlCommentsPerPost": True,
            "maxPostsCount": 0,                  # only need comments back; posts are already in our DB
            "maxCommentsPerPost": max_comments,
            "maxCommentsCount": 0,
            "maxCommunitiesCount": 0,
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
        result = self._apify_run("comments", payload, token=token)
        if result is None:
            return {}
        items = result["items"]
        print(f"[reddit:apify] comments ok: {len(items)} items "
              f"(run_id={result['run_id']}, usage=${result['usage_usd']})")
        # Build {postId: post_author} so we can stamp is_op without needing post objects upstream.
        post_authors: dict[str, str] = {}
        for it in items:
            if (it.get("dataType") or "") != "post":
                continue
            pid = (it.get("parsedId") or it.get("id") or "").lstrip("t3_")
            if pid and it.get("authorName"):
                post_authors[pid] = it["authorName"]
        # Build {postId: list_of_comment_dicts}
        by_post: dict[str, list[dict]] = {}
        for it in items:
            if (it.get("dataType") or "") != "comment":
                continue
            pid_raw = it.get("parsedPostId") or it.get("postId") or it.get("parentId") or ""
            pid = pid_raw[3:] if pid_raw.startswith("t3_") else pid_raw
            if not pid:
                continue
            mapped = self._apify_comment_dict(it, post_authors.get(pid))
            if mapped is None:
                continue
            by_post.setdefault(pid, []).append(mapped)
        # Sort per-post by score desc, cap at max_comments. Then key by permalink (which is how
        # _enrich_top_with_comments looks them up).
        out: dict[str, list[dict]] = {}
        for url in post_urls:
            permalink = ""
            if "reddit.com" in url:
                idx = url.find("/r/")
                if idx != -1:
                    permalink = url[idx:]
            if not permalink:
                continue
            # Recover postId from the permalink (`/r/<sub>/comments/<id>/<slug>/`).
            parts = [p for p in permalink.split("/") if p]
            pid = None
            try:
                pid = parts[parts.index("comments") + 1]
            except (ValueError, IndexError):
                pid = None
            if not pid:
                continue
            lst = by_post.get(pid, [])
            lst.sort(key=lambda c: c["score"], reverse=True)
            cl = lst[:max_comments]
            if cl:
                out[permalink] = cl
                self._apify_comments_by_permalink[permalink] = cl
        return out

    def _apify_comment_dict(self, c: dict, op_author: str | None) -> dict | None:
        """Map a harshmaur comment object → our canonical comment dict (same shape that
        parse_old_reddit_comments returns, so downstream code is identity).
        """
        body = (c.get("body") or "").strip()
        author = c.get("authorName") or c.get("author") or ""
        if not body or body in _DEAD_BODY_STRINGS or author in _COMMENT_AUTHOR_BLOCKLIST:
            return None
        if len(body) > _BODY_MAX_CHARS:
            body = body[:_BODY_MAX_CHARS].rstrip() + "…(truncated)"
        cid = c.get("parsedId") or c.get("id") or ""
        fullname = cid if cid.startswith("t1_") else (f"t1_{cid}" if cid else "")
        # harshmaur doesn't expose `is_submitter` directly; we compute via author equality.
        is_op = bool(op_author) and author == op_author
        return {
            "id": fullname[3:] if fullname.startswith("t1_") else cid,
            "fullname": fullname,
            "author": author,
            "score": int(c.get("commentUpVotes") or c.get("score") or 0),
            "body": body,
            "is_op": is_op,
            "replies": 0,
        }

    # ---- mode: old_html (deprecated 2026-05-31; kept as a fallback when APIFY_TOKEN absent) ----
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
        # Apify path: comments were fetched inline by `_fetch_apify` and cached. Serve from
        # memory — `_enrich_top_with_comments` (runner.py) is the only caller and it loops over
        # Top-N items; we trade a network round-trip for an O(1) dict lookup.
        if self.mode == "apify":
            cached = self._apify_comments_by_permalink.get(permalink, [])
            return cached[:max_comments]
        # Old.reddit fallback: re-fetch the post page and parse.
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
