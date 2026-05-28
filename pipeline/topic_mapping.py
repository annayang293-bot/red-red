"""4-step topic-mapping algorithm (Step 3).

Input a topic keyword → output "which subreddits to fetch from". Replaces legacy's hardcoded 6 subreddits.

4 steps (PRD §4):
  ① Candidate generation: Reddit subreddit search + LLM recommendations + synonym expansion
  ② Score & rank: relevance × quality (subscriber count)
  ③ Quality gate + safety net: operator allow/deny (whitelist forces inclusion / blacklist permanently drops) + edge-case safety net
  ④ Cache: 7d TTL + 30d hard ceiling + stale judgment + --no-cache force refresh

Design: external dependencies (Reddit search / LLM / cache storage) are all **injectable**, so:
  - tests use stubs (no API spend, no network dependency);
  - Step 4 wires in real implementations (real Reddit search + real OpenAI + Supabase cache) without
    touching the core algorithm.

Cache key invariants (mirroring the topics_cache table): TTL (expires_at) triggers recompute on expiry;
but hard_ceiling_at is only set on "first derivation / hard-ceiling exceeded" — **TTL refresh does not
push it back** — otherwise the 30-day hard cap loses meaning.
"""
from __future__ import annotations

import abc
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

# ---- Default parameters (overridable in TopicMapper's constructor) ----
DEFAULT_TARGET_COUNT = 6      # How many subreddits to return finally (aligned with legacy's 6)
DEFAULT_MIN_COUNT = 3         # Below this triggers the "edge-case safety net" warning
DEFAULT_TTL_DAYS = 7
DEFAULT_HARD_CEILING_DAYS = 30
QUALITY_FULL_SUBS = 1_000_000  # Subscriber count at this level → quality ≈ 1.0
REL_WEIGHT = 0.65             # Weight of relevance in the composite score
QUAL_WEIGHT = 0.35            # Weight of quality in the composite score

# Injected-dependency signatures:
#   reddit_search_fn(keyword, limit) -> list[{"name","subscribers"(int|None),...}] (sorted by relevance)
#   llm_suggest_fn(keyword) -> list[str] (recommended subreddit names, may include synonym expansions)
RedditSearchFn = Callable[[str, int], list[dict]]
LLMSuggestFn = Callable[[str], list[str]]


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _norm(name: str) -> str:
    """Normalize a subreddit name (strip r/ prefix, strip whitespace, lowercase) for matching / dedup."""
    n = (name or "").strip()
    if n.lower().startswith("r/"):
        n = n[2:]
    if n.startswith("/"):
        n = n[1:]
    return n.strip().lower()


@dataclass
class SubredditCandidate:
    name: str                       # Display name (original)
    relevance: float = 0.0          # 0–1
    quality: float = 0.0            # 0–1 (derived from subscriber count)
    score: float = 0.0              # 0–1 composite
    subscribers: Optional[int] = None
    sources: list = field(default_factory=list)  # ['search','llm','synonym']
    forced: bool = False            # Forced inclusion from allow_list
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "relevance": round(self.relevance, 4),
            "quality": round(self.quality, 4), "score": round(self.score, 4),
            "subscribers": self.subscribers, "sources": sorted(set(self.sources)),
            "forced": self.forced, "reason": self.reason,
        }


@dataclass
class MappingResult:
    topic_keyword: str
    subreddits: list           # list[SubredditCandidate] (sorted, final result)
    from_cache: bool = False
    stale: bool = False
    cached_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    hard_ceiling_at: Optional[datetime] = None
    generated_at: Optional[datetime] = None
    allow_list_applied: list = field(default_factory=list)
    deny_list_applied: list = field(default_factory=list)
    warnings: list = field(default_factory=list)  # Structured warnings list (Rex Step 3 🟡);
    # `stale` is already its own bool field; don't duplicate it as a string in warnings — callers
    # should look at .stale directly.

    @property
    def subreddit_names(self) -> list:
        return [c.name for c in self.subreddits]


# ---------------- Cache storage (interface + in-memory impl) ----------------
class CacheStore(abc.ABC):
    """Abstraction for topics_cache. Step 4/6 wires the Supabase implementation; tests use InMemory."""

    @abc.abstractmethod
    def get(self, topic_keyword: str) -> Optional[dict]:
        ...

    @abc.abstractmethod
    def set(self, topic_keyword: str, payload: dict) -> None:
        ...


class InMemoryCacheStore(CacheStore):
    def __init__(self):
        self._d: dict[str, dict] = {}

    def get(self, topic_keyword: str) -> Optional[dict]:
        return self._d.get(_norm(topic_keyword))

    def set(self, topic_keyword: str, payload: dict) -> None:
        self._d[_norm(topic_keyword)] = payload


# ---------------- Main algorithm ----------------
class TopicMapper:
    def __init__(
        self,
        reddit_search_fn: RedditSearchFn,
        llm_suggest_fn: Optional[LLMSuggestFn] = None,
        cache: Optional[CacheStore] = None,
        *,
        target_count: int = DEFAULT_TARGET_COUNT,
        min_count: int = DEFAULT_MIN_COUNT,
        ttl_days: int = DEFAULT_TTL_DAYS,
        hard_ceiling_days: int = DEFAULT_HARD_CEILING_DAYS,
        search_limit: int = 25,
    ):
        if reddit_search_fn is None:
            raise ValueError("reddit_search_fn is required (Step 4 wires in real Reddit search)")
        self.reddit_search_fn = reddit_search_fn
        self.llm_suggest_fn = llm_suggest_fn
        self.cache = cache or InMemoryCacheStore()
        self.target_count = target_count
        self.min_count = min_count
        self.ttl_days = ttl_days
        self.hard_ceiling_days = hard_ceiling_days
        self.search_limit = search_limit

    # ---- Public entry point ----
    def map_topic(
        self,
        keyword: str,
        *,
        topic_id: Optional[int] = None,
        allow_list: Optional[set] = None,
        deny_list: Optional[set] = None,
        no_cache: bool = False,
        now: Optional[datetime] = None,
    ) -> MappingResult:
        if not keyword or not keyword.strip():
            raise ValueError("keyword cannot be empty")
        keyword = keyword.strip()
        now = _now(now)
        allow = {_norm(x) for x in (allow_list or set())}
        deny = {_norm(x) for x in (deny_list or set())}

        # The cache only stores the "pure candidate pool" (steps ①②); the operator (step ③) is
        # re-applied on every call. That way allow/deny (and their topic scope) are dynamic and
        # cannot be polluted by a previous call's operator decision.
        prior = self.cache.get(keyword)   # Always read: needed for hard-ceiling carry + stale fallback

        # Hit + within TTL → use the cached pure candidates + re-finalize with this call's operator (unless --no-cache)
        if prior and not no_cache:
            exp = _as_dt(prior.get("expires_at"))
            if exp and now < exp:
                return self._finalize_from_cache(prior, allow, deny, now, stale=False)

        # Need to (re-)derive steps ①②
        try:
            cand = self._generate_and_score(keyword)
        except Exception as e:
            # Derivation failed: if there's an old cache within hard_ceiling (and not --no-cache) →
            # fall back to stale cache; past the ceiling or no cache or --no-cache → fail loud.
            if prior and not no_cache:
                hc = _as_dt(prior.get("hard_ceiling_at"))
                if hc and now < hc:
                    print(f"[topic_mapping] re-derivation failed, falling back to stale cache (within hard ceiling): {e}")
                    return self._finalize_from_cache(prior, allow, deny, now, stale=True)
            raise

        # Write cache: only the pure candidate pool + timestamps (hard-ceiling renewal does NOT push it back)
        cached_at = now
        expires_at = now + timedelta(days=self.ttl_days)
        hard_ceiling_at = self._carry_hard_ceiling(prior, now)
        self.cache.set(keyword, {
            "topic_keyword": keyword,
            "candidates": [c.to_dict() for c in cand.values()],
            "cached_at": cached_at, "expires_at": expires_at,
            "hard_ceiling_at": hard_ceiling_at,
        })
        # Step ③ + sort & truncate
        return self._finalize(keyword, cand, allow, deny, now, from_cache=False,
                              cached_at=cached_at, expires_at=expires_at,
                              hard_ceiling_at=hard_ceiling_at)

    # ---- ① Candidate generation ----
    def _generate_candidates(self, keyword: str) -> dict:
        cand: dict[str, SubredditCandidate] = {}

        def add(name, source, base_rel, subs=None):
            key = _norm(name)
            if not key:
                return
            c = cand.get(key)
            if c is None:
                c = SubredditCandidate(name=_display_name(name))
                cand[key] = c
            if source not in c.sources:
                c.sources.append(source)
            c.relevance = max(c.relevance, base_rel)
            if subs is not None and (c.subscribers is None or subs > c.subscribers):
                c.subscribers = subs

        # Reddit subreddit search (sorted by relevance; earlier position → higher relevance)
        results = self.reddit_search_fn(keyword, self.search_limit) or []
        n = max(len(results), 1)
        for idx, r in enumerate(results):
            name = r.get("name") or r.get("display_name") or ""
            subs = r.get("subscribers")
            pos_rel = 1.0 - 0.5 * (idx / n)            # 0.5–1.0 by position
            kw_bonus = 0.1 if _kw_overlap(keyword, name) else 0.0
            add(name, "search", min(1.0, pos_rel + kw_bonus), subs)

        # LLM suggestions + synonym expansion (optional; skipped if no llm_fn)
        if self.llm_suggest_fn:
            try:
                suggestions = self.llm_suggest_fn(keyword) or []
            except Exception as e:  # LLM failure is non-fatal: degrade to search-only candidates
                suggestions = []
                print(f"[topic_mapping] LLM suggestion failed, degrading to search-only candidates: {e}")
            for name in suggestions:
                add(name, "llm", 0.8)
        return cand

    # ---- ② Scoring ----
    def _score_all(self, cand: dict) -> None:
        for c in cand.values():
            c.quality = _quality_from_subs(c.subscribers)
            c.score = REL_WEIGHT * c.relevance + QUAL_WEIGHT * c.quality

    # ---- ③ Quality gate + operator safety net ----
    def _apply_operator(self, cand: dict, allow_list: set, deny_list: set):
        deny_applied, allow_applied = [], []
        # Blacklist: permanently drop
        for key in list(cand.keys()):
            if key in deny_list:
                deny_applied.append(cand[key].name)
                del cand[key]
        # Whitelist: forced inclusion (add even if not in candidates; if present, max it out + tag forced)
        for key in allow_list:
            if key in deny_list:
                continue  # deny wins, to avoid contradictions
            c = cand.get(key)
            if c is None:
                c = SubredditCandidate(name=_display_name(key), relevance=1.0,
                                       quality=0.5, sources=["allow_list"])
                cand[key] = c
            c.forced = True
            c.score = 1.0
            c.reason = (c.reason + " " if c.reason else "") + "forced inclusion via operator allow-list"
            allow_applied.append(c.name)
        return allow_applied, deny_applied

    def _generate_and_score(self, keyword) -> dict:
        """Steps ①②: candidate generation + scoring. Returns the pure candidate pool (operator not applied), cacheable."""
        cand = self._generate_candidates(keyword)
        self._score_all(cand)
        return cand

    def _finalize(self, keyword, cand, allow, deny, now, *, from_cache,
                  cached_at, expires_at, hard_ceiling_at, stale=False) -> MappingResult:
        """Step ③ + sort & truncate + assemble result. Every call uses the **current** operator context (decisions not cached).

        Note: this mutates `cand` in place (insertions/deletions/flag `forced`), so cand must be a
        per-call exclusive copy (the derivation path produces a fresh one; the cache-hit path rebuilds
        via _candidate_from_dict, also fresh).
        """
        allow_applied, deny_applied = self._apply_operator(cand, allow, deny)
        ranked = sorted(cand.values(), key=lambda c: (c.forced, c.score), reverse=True)
        final = ranked[: self.target_count]

        # Structured warnings (Rex Step 3 🟡): only real warning strings here; staleness is via the
        # .stale bool field; don't duplicate "stale:..." string in warnings.
        warnings: list[str] = []
        if len(final) < self.min_count:
            warnings.append(
                f"edge case: only {len(final)} subreddits mapped (< {self.min_count}); "
                f"operator should add allow_list entries or pick a more specific topic keyword")

        return MappingResult(
            topic_keyword=keyword, subreddits=final, from_cache=from_cache, stale=stale,
            cached_at=cached_at, expires_at=expires_at, hard_ceiling_at=hard_ceiling_at,
            generated_at=(None if from_cache else now),
            allow_list_applied=allow_applied, deny_list_applied=deny_applied,
            warnings=warnings,
        )

    def _carry_hard_ceiling(self, prior, now) -> datetime:
        """The hard ceiling is reset only on first derivation / when exceeded; TTL refresh preserves the original value."""
        fresh = now + timedelta(days=self.hard_ceiling_days)
        if not prior:
            return fresh
        prior_hc = _as_dt(prior.get("hard_ceiling_at"))
        if prior_hc is None or now >= prior_hc:
            return fresh        # No prior value, or past the ceiling → reset
        return prior_hc          # TTL refresh: keep the original ceiling, do not push it back

    def _finalize_from_cache(self, prior, allow, deny, now, *, stale) -> MappingResult:
        """Cache hit: rebuild from the cached pure candidate pool → apply **this call's** operator → finalize."""
        cand = {}
        for d in prior.get("candidates", []):
            c = _candidate_from_dict(d)
            cand[_norm(c.name)] = c
        return self._finalize(
            prior.get("topic_keyword", ""), cand, allow, deny, now,
            from_cache=True, stale=stale,
            cached_at=_as_dt(prior.get("cached_at")),
            expires_at=_as_dt(prior.get("expires_at")),
            hard_ceiling_at=_as_dt(prior.get("hard_ceiling_at")),
        )


# ---------------- Helpers ----------------
def _display_name(name: str) -> str:
    n = (name or "").strip()
    if n.lower().startswith("r/"):
        n = n[2:]
    return n.lstrip("/").strip()


def _kw_overlap(keyword: str, name: str) -> bool:
    toks = {t for t in keyword.lower().replace("/", " ").split() if t}
    nm = _norm(name)
    return any(t in nm for t in toks)


def _quality_from_subs(subs: Optional[int]) -> float:
    if not subs or subs <= 0:
        return 0.5  # Unknown subscriber count → neutral, no reward or penalty
    return min(1.0, math.log10(subs + 1) / math.log10(QUALITY_FULL_SUBS))


def _as_dt(v) -> Optional[datetime]:
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _candidate_from_dict(d: dict) -> SubredditCandidate:
    return SubredditCandidate(
        name=d.get("name", ""), relevance=d.get("relevance", 0.0),
        quality=d.get("quality", 0.0), score=d.get("score", 0.0),
        subscribers=d.get("subscribers"), sources=list(d.get("sources") or []),
        forced=bool(d.get("forced")), reason=d.get("reason", ""),
    )


def default_reddit_search(keyword: str, limit: int = 25, *,
                          ua: Optional[str] = None,
                          base: str = "https://www.reddit.com") -> list:
    """Real Reddit subreddit search (public endpoint). Step 4 can inject this as reddit_search_fn.

    Returns [{"name","subscribers","title"}] sorted by Reddit relevance.
    Note: as of 2026 the anonymous public endpoint is rate-limited / blocked by Reddit (Richard's research)
    → Step 4 switches to OAuth; this is kept as default / degradation, and raises on failure so the
    caller can decide how to degrade.
    """
    import requests  # Lazy import: pure-algorithm tests don't need requests
    headers = {"User-Agent": ua or "python:system1-app:v0.1 (by /u/CHANGE_ME)"}
    url = f"{base}/subreddits/search.json"
    r = requests.get(url, headers=headers,
                     params={"q": keyword, "limit": min(limit, 100), "raw_json": 1},
                     timeout=25)
    r.raise_for_status()
    out = []
    for child in r.json().get("data", {}).get("children", []):
        d = child.get("data", {})
        name = d.get("display_name")
        if not name:
            continue
        out.append({"name": name, "subscribers": d.get("subscribers"),
                    "title": d.get("title")})
    return out


def resolve_operator_lists(entries: list, topic_id: Optional[int] = None) -> tuple:
    """Parse rows from operator_lists (global + this topic's scope) into (allow_set, deny_set).

    entries: [{"list_type":"allow|deny","subreddit_name":..,"scope_topic_id":int|None}]
    scope_topic_id=None → global; otherwise applies only to that topic_id.
    """
    allow, deny = set(), set()
    for e in entries or []:
        scope = e.get("scope_topic_id")
        if scope is not None and scope != topic_id:
            continue
        name = _norm(e.get("subreddit_name", ""))
        if not name:
            continue
        if e.get("list_type") == "allow":
            allow.add(name)
        elif e.get("list_type") == "deny":
            deny.add(name)
    return allow, deny
