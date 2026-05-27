"""主题映射 4 步算法(Step 3)。

输入一个主题词 → 输出"该去哪些 subreddit 抓"。替换 legacy 的硬编码 6 个版块。

4 步(PRD §4):
  ① 候选生成   : Reddit 版块搜索 + LLM 推荐 + 同义词扩展
  ② 打分排序   : 相关性 × 质量(订阅数)
  ③ 质量闸+兜底: operator allow/deny(白名单强制纳入 / 黑名单永久剔除)+ 边缘 case 兜底
  ④ 缓存       : 7 天 TTL + 30 天 hard ceiling + stale 判断 + --no-cache 强刷

设计:外部依赖(Reddit 搜索 / LLM / 缓存存储)全部**可注入**,便于:
  - 用 stub 跑单测,不烧 API / 不依赖网络;
  - Step 4 接真实实现(真 Reddit 搜索 + 真 OpenAI + Supabase 缓存),主算法不改。

缓存关键不变量(对应 topics_cache 表):TTL(expires_at)到期会重算;但 hard_ceiling_at
只在"首次派生 / 超过硬上限"时设置,**TTL 续期不往后推**——否则 30 天硬上限失去意义。
"""
from __future__ import annotations

import abc
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

# ---- 默认参数(可在 TopicMapper 构造时覆盖) ----
DEFAULT_TARGET_COUNT = 6      # 最终返回多少个 subreddit(对齐 legacy 的 6 个)
DEFAULT_MIN_COUNT = 3         # 低于这个数触发"边缘 case 兜底"告警
DEFAULT_TTL_DAYS = 7
DEFAULT_HARD_CEILING_DAYS = 30
QUALITY_FULL_SUBS = 1_000_000  # 订阅数到这个量级 → quality≈1.0
REL_WEIGHT = 0.65             # 综合分里相关性权重
QUAL_WEIGHT = 0.35            # 综合分里质量权重

# 注入式依赖签名:
#   reddit_search_fn(keyword, limit) -> list[{"name","subscribers"(int|None),...}](按相关性排序)
#   llm_suggest_fn(keyword) -> list[str](推荐的 subreddit 名,可含同义词扩展结果)
RedditSearchFn = Callable[[str, int], list[dict]]
LLMSuggestFn = Callable[[str], list[str]]


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _norm(name: str) -> str:
    """subreddit 名归一化(去 r/ 前缀、去空白、小写)用于匹配/去重。"""
    n = (name or "").strip()
    if n.lower().startswith("r/"):
        n = n[2:]
    if n.startswith("/"):
        n = n[1:]
    return n.strip().lower()


@dataclass
class SubredditCandidate:
    name: str                       # 展示用原名
    relevance: float = 0.0          # 0–1
    quality: float = 0.0            # 0–1(由订阅数推)
    score: float = 0.0              # 0–1 综合
    subscribers: Optional[int] = None
    sources: list = field(default_factory=list)  # ['search','llm','synonym']
    forced: bool = False            # 来自 allow_list 强制纳入
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
    subreddits: list           # list[SubredditCandidate](已排序,最终结果)
    from_cache: bool = False
    stale: bool = False
    cached_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    hard_ceiling_at: Optional[datetime] = None
    generated_at: Optional[datetime] = None
    allow_list_applied: list = field(default_factory=list)
    deny_list_applied: list = field(default_factory=list)
    warnings: list = field(default_factory=list)  # 结构化告警列表(Rex Step3 🟡);
    # stale 已是独立 bool 字段,不再在 warnings 里重复字符串,调用方直接看 .stale。

    @property
    def subreddit_names(self) -> list:
        return [c.name for c in self.subreddits]


# ---------------- 缓存存储(接口 + 内存实现) ----------------
class CacheStore(abc.ABC):
    """topics_cache 的抽象。Step 4/6 接 Supabase 实现;测试用 InMemory。"""

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


# ---------------- 主算法 ----------------
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
            raise ValueError("reddit_search_fn 必填(Step 4 接真实 Reddit 搜索)")
        self.reddit_search_fn = reddit_search_fn
        self.llm_suggest_fn = llm_suggest_fn
        self.cache = cache or InMemoryCacheStore()
        self.target_count = target_count
        self.min_count = min_count
        self.ttl_days = ttl_days
        self.hard_ceiling_days = hard_ceiling_days
        self.search_limit = search_limit

    # ---- 对外入口 ----
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
            raise ValueError("keyword 不能为空")
        keyword = keyword.strip()
        now = _now(now)
        allow = {_norm(x) for x in (allow_list or set())}
        deny = {_norm(x) for x in (deny_list or set())}

        # 缓存只存"纯候选池"(step ①②);operator(step ③)每次调用都重新套。
        # 这样 allow/deny(及其 topic scope)是动态的,不会被上次调用的 operator 决策污染。
        prior = self.cache.get(keyword)   # 总是读:用于 hard ceiling carry + stale 回退

        # 命中且 TTL 内 → 用缓存的纯候选 + 本次 operator 重新 finalize(非 --no-cache)
        if prior and not no_cache:
            exp = _as_dt(prior.get("expires_at"))
            if exp and now < exp:
                return self._finalize_from_cache(prior, allow, deny, now, stale=False)

        # 需要(重新)派生 step ①②
        try:
            cand = self._generate_and_score(keyword)
        except Exception as e:
            # 派生失败:有旧缓存且在 hard ceiling 内(且非 --no-cache)→ 回退到 stale 缓存;
            # 超硬上限或无缓存或 --no-cache → fail loud。
            if prior and not no_cache:
                hc = _as_dt(prior.get("hard_ceiling_at"))
                if hc and now < hc:
                    print(f"[topic_mapping] 重新派生失败,回退 stale 缓存(hard ceiling 内): {e}")
                    return self._finalize_from_cache(prior, allow, deny, now, stale=True)
            raise

        # 写缓存:只存纯候选池 + 时间(hard ceiling 续期不往后推)
        cached_at = now
        expires_at = now + timedelta(days=self.ttl_days)
        hard_ceiling_at = self._carry_hard_ceiling(prior, now)
        self.cache.set(keyword, {
            "topic_keyword": keyword,
            "candidates": [c.to_dict() for c in cand.values()],
            "cached_at": cached_at, "expires_at": expires_at,
            "hard_ceiling_at": hard_ceiling_at,
        })
        # step ③ + 排序截断
        return self._finalize(keyword, cand, allow, deny, now, from_cache=False,
                              cached_at=cached_at, expires_at=expires_at,
                              hard_ceiling_at=hard_ceiling_at)

    # ---- ① 候选生成 ----
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

        # Reddit 版块搜索(按相关性排序;位置越靠前 relevance 越高)
        results = self.reddit_search_fn(keyword, self.search_limit) or []
        n = max(len(results), 1)
        for idx, r in enumerate(results):
            name = r.get("name") or r.get("display_name") or ""
            subs = r.get("subscribers")
            pos_rel = 1.0 - 0.5 * (idx / n)            # 0.5–1.0 按位置
            kw_bonus = 0.1 if _kw_overlap(keyword, name) else 0.0
            add(name, "search", min(1.0, pos_rel + kw_bonus), subs)

        # LLM 推荐 + 同义词扩展(可选;无 llm_fn 则跳过)
        if self.llm_suggest_fn:
            try:
                suggestions = self.llm_suggest_fn(keyword) or []
            except Exception as e:  # LLM 失败不致命:降级到仅搜索候选
                suggestions = []
                print(f"[topic_mapping] LLM 推荐失败,降级到仅搜索候选: {e}")
            for name in suggestions:
                add(name, "llm", 0.8)
        return cand

    # ---- ② 打分 ----
    def _score_all(self, cand: dict) -> None:
        for c in cand.values():
            c.quality = _quality_from_subs(c.subscribers)
            c.score = REL_WEIGHT * c.relevance + QUAL_WEIGHT * c.quality

    # ---- ③ 质量闸 + operator 兜底 ----
    def _apply_operator(self, cand: dict, allow_list: set, deny_list: set):
        deny_applied, allow_applied = [], []
        # 黑名单:永久剔除
        for key in list(cand.keys()):
            if key in deny_list:
                deny_applied.append(cand[key].name)
                del cand[key]
        # 白名单:强制纳入(不在候选里也加;在的话拉满 + 标 forced)
        for key in allow_list:
            if key in deny_list:
                continue  # deny 优先,避免自相矛盾
            c = cand.get(key)
            if c is None:
                c = SubredditCandidate(name=_display_name(key), relevance=1.0,
                                       quality=0.5, sources=["allow_list"])
                cand[key] = c
            c.forced = True
            c.score = 1.0
            c.reason = (c.reason + " " if c.reason else "") + "operator 白名单强制纳入"
            allow_applied.append(c.name)
        return allow_applied, deny_applied

    def _generate_and_score(self, keyword) -> dict:
        """step ①②:候选生成 + 打分。返回纯候选池(未套 operator),可缓存。"""
        cand = self._generate_candidates(keyword)
        self._score_all(cand)
        return cand

    def _finalize(self, keyword, cand, allow, deny, now, *, from_cache,
                  cached_at, expires_at, hard_ceiling_at, stale=False) -> MappingResult:
        """step ③ + 排序截断 + 组装结果。每次调用都用**当前** operator 上下文(不缓存决策)。

        注:会就地修改 cand(增删/标记 forced),所以 cand 必须是本次调用独占的副本
        (派生路径天然是新的;缓存命中路径用 _candidate_from_dict 重建,也是新的)。
        """
        allow_applied, deny_applied = self._apply_operator(cand, allow, deny)
        ranked = sorted(cand.values(), key=lambda c: (c.forced, c.score), reverse=True)
        final = ranked[: self.target_count]

        # 结构化告警(Rex Step3 🟡):只放真正的告警字符串;staleness 看 .stale bool,
        # 不在 warnings 里重复"stale:..."字符串。
        warnings: list[str] = []
        if len(final) < self.min_count:
            warnings.append(
                f"边缘 case:映射出的版块只有 {len(final)} 个(< {self.min_count}),"
                f"建议 operator 补 allow_list 或换更具体的主题词")

        return MappingResult(
            topic_keyword=keyword, subreddits=final, from_cache=from_cache, stale=stale,
            cached_at=cached_at, expires_at=expires_at, hard_ceiling_at=hard_ceiling_at,
            generated_at=(None if from_cache else now),
            allow_list_applied=allow_applied, deny_list_applied=deny_applied,
            warnings=warnings,
        )

    def _carry_hard_ceiling(self, prior, now) -> datetime:
        """hard ceiling 只在首次派生 / 超过硬上限时重置;TTL 续期保留原值。"""
        fresh = now + timedelta(days=self.hard_ceiling_days)
        if not prior:
            return fresh
        prior_hc = _as_dt(prior.get("hard_ceiling_at"))
        if prior_hc is None or now >= prior_hc:
            return fresh        # 没有旧值,或已超硬上限 → 重置
        return prior_hc          # TTL 续期:沿用原硬上限,不往后推

    def _finalize_from_cache(self, prior, allow, deny, now, *, stale) -> MappingResult:
        """缓存命中:从缓存的纯候选池重建 → 套**本次**调用的 operator → finalize。"""
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


# ---------------- 小工具 ----------------
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
        return 0.5  # 未知订阅数 → 中性值,不奖不罚
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
    """真实 Reddit 版块搜索(公开 endpoint)。Step 4 可把它作为 reddit_search_fn 注入。

    返回 [{"name","subscribers","title"}],按 Reddit 相关性排序。
    注:匿名公开接口 2026 已被 Reddit 限流/封(Richard 调研)→ Step 4 改走 OAuth;
    这里保留公开版作默认/降级,失败抛异常由调用方决定降级。
    """
    import requests  # 延迟导入:纯算法测试不依赖 requests
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
    """把 operator_lists 表的行(global + 本主题 scope)解析成 (allow_set, deny_set)。

    entries: [{"list_type":"allow|deny","subreddit_name":..,"scope_topic_id":int|None}]
    scope_topic_id=None → 全局生效;否则只对该 topic_id 生效。
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
