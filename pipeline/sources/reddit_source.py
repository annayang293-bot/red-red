"""Reddit 源 —— 两种取数模式(config.reddit.auth_mode):

- "public"(默认,无需建 app):走 https://www.reddit.com/r/<sub>/<listing>.json
  只读公开数据,不需要 client_id/secret/OAuth。Reddit 礼貌要求带描述性 User-Agent。
- "oauth"(更稳健):application-only OAuth(client_credentials),需 .env 设
  REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET。

健壮性:UA 校验;限流/瞬时错误指数退避 + 尊重 Retry-After;失败 sub 记入
failed_subs 不静默。
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import requests

from .base import Source
from ..schema import HotItem, make_id, canonical_url, clip_snippet, now_iso, to_iso

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
OAUTH_BASE = "https://oauth.reddit.com"
PUBLIC_BASE = "https://www.reddit.com"
DEFAULT_UA = "python:system1-app:v0.1 (by /u/CHANGE_ME)"
_BAD_UA_TOKENS = ("CHANGE_ME", "yourname", "<realuser>", "<user>")
_MAX_RETRIES = 3


class RedditSource(Source):
    name = "reddit"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.rc = cfg["reddit"]
        self.mode = (self.rc.get("auth_mode") or "public").lower()
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
                "Reddit User-Agent 不合规。请在 .env 设 REDDIT_USER_AGENT,"
                "格式: 'python:system1-app:v0.1 (by /u/你的真实Reddit用户名)' "
                f"(当前: {ua!r})"
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
        raise last if last else RuntimeError(f"请求失败: {url}")

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        cid = os.environ.get("REDDIT_CLIENT_ID")
        csec = os.environ.get("REDDIT_CLIENT_SECRET")
        if not cid or not csec:
            raise RuntimeError(
                "oauth 模式缺少 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET。"
                "若暂时无法创建 app,可设 reddit.auth_mode: public 先跑通。"
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

    # ---- fetch ----
    def _listing_path(self, sub: str) -> str:
        listing = self.rc.get("listing", "hot")
        if self.mode == "oauth":
            return f"{OAUTH_BASE}/r/{sub}/{listing}"
        return f"{PUBLIC_BASE}/r/{sub}/{listing}.json"

    def fetch(self):
        self._validate_ua()
        if self.mode == "oauth":
            token = self._get_token()
            headers = {"Authorization": f"Bearer {token}", "User-Agent": self._ua()}
        else:
            headers = {"User-Agent": self._ua()}  # public: 无需 token/凭证
        limit = int(self.rc.get("fetch_limit_per_sub", 60))
        proxy_field = self.rc.get("saveshare_proxy_field", "num_crossposts")
        self.failed_subs = []
        items: list[HotItem] = []
        for sub in self.rc.get("subreddits", []):
            params = {"limit": min(limit, 100), "raw_json": 1}
            if self.rc.get("listing") == "top":
                params["t"] = self.rc.get("time_filter", "day")
            try:
                r = self._request_with_retry(
                    "GET", self._listing_path(sub), headers=headers, params=params)
            except Exception as e:
                self.failed_subs.append(sub)
                print(f"[reddit:{self.mode}] r/{sub} 拉取失败(已重试): {e}")
                continue
            excluded_flairs = [f.lower() for f in self.rc.get("excluded_flairs", [])]
            for child in r.json().get("data", {}).get("children", []):
                d = child.get("data", {})
                if d.get("stickied"):
                    continue
                _flair = (d.get("link_flair_text") or "").lower()
                if _flair and any(bad in _flair for bad in excluded_flairs):
                    continue  # flair 黑名单: 过滤 meme/joke 等
                native_id = d.get("id", "")
                permalink = d.get("permalink", "")
                link = f"https://www.reddit.com{permalink}" if permalink else d.get("url", "")
                # 跳过缺关键主键字段的记录,避免空 source_native_id / 空 url 撞约束或 hash 碰撞
                if not native_id or not link:
                    print(f"[reddit:{self.mode}] 跳过缺 id/url 的帖子: {(d.get('title') or '')[:50]!r}")
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
                    lang="en",  # V1 简化: 当前 subreddit 全英文; 需要时再扩展多语言
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
