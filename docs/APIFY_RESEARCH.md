# Apify Reddit actor — research & decision

> **Decision (2026-05-31)**: use **`harshmaur/reddit-scraper`** in a two-call **split** pattern:
> one Apify run for the multi-sub listing, one Apify run later for Top-N comments. Verified
> end-to-end against the real Apify API with this account's token; production-load timing &
> cost numbers below come from those probes.
>
> `fatihtahta/reddit-scraper-search-fast` was the initial pick (canonical Reddit field names +
> cheapest per-result rate), but its `urls` input rejects multi-target batches at runtime — that
> kills the split-pattern cost story, and we don't want N sequential per-post Apify runs (each
> with ~30-60s start latency). harshmaur supports batched `startUrls` for both subreddit listing
> URLs and post permalink URLs; that's the deciding factor.

---

## Method

Token from `~/Projects/xhs-ai-ip/system1-app/.env` (mode 600, never echoed). Each candidate ran
via `POST /v2/acts/<actor>/runs` against a synthetic load matching one production pipeline pass.
Wall-clock and `usageTotalUsd` captured from `/v2/acts/.../runs/<id>`.

## Actors evaluated

| Actor                                       | Outcome                       | Why ruled in / out                               |
|---------------------------------------------|-------------------------------|--------------------------------------------------|
| `trudax/reddit-scraper` (full)              | 403 `actor-is-not-rented`     | $45/mo rental, free trial expired on this account |
| `epctex/reddit-scraper`                     | 403 same                      | $40/mo rental, free trial expired                 |
| `comchat/reddit-api-scraper`                | 403 needs account grant       | Required full Apify-account write permissions — declined on security grounds |
| `automation-lab/reddit-scraper`             | Ran 0 items in 2s             | Input schema undocumented; couldn't coax it into returning anything useful |
| `trudax/reddit-scraper-lite`                | Ran; **post schema incomplete** | Verified twice with `clean=false`: post objects lack `upVotes` **and** `numberOfComments` despite the Store page docs claiming they exist. Without those, `hot_score` cannot be computed → unusable.  |
| `fatihtahta/reddit-scraper-search-fast`     | Ran with `subredditName`, fails on multi-target `urls` | Canonical Reddit field names + cheapest at $0.00149/item, but only accepts ONE subreddit per call. Multi-target via `urls` returned FAILED in 11s for both subreddit URLs and post URLs. Per-sub serial runs would be 6 × 80s ≈ 8 min wall-clock — not viable. |
| **`harshmaur/reddit-scraper`**              | **Both batched calls work**   | Multi-sub listing (6 subreddit URLs in one call, `crawlCommentsPerPost: false`, `maxPostsCount: 30`) returns 180 items in 43s at $0.32. Multi-post comments (2+ post URLs in one call, `crawlCommentsPerPost: true`) returns posts + threaded comments at $0.032 for 6 items. |

## Production load measurements (harshmaur, real probe data)

| Call | Input | Wall | Items | Cost | $/item |
|---|---|---:|---:|---:|---:|
| Listing | 6 sub URLs × `maxPostsCount: 30` | **43s** | 180 posts | **$0.32** | $0.0018 |
| Comments | 2 post URLs × `maxCommentsPerPost: 5` | 53s | 1 post + 5 comments | $0.032 | $0.0053 |

The comments-call cost scales roughly linearly with item count, so the projected ~20 post URLs ×
~10 comments each ≈ 220 items would land around **$0.40-0.50 per run**.

**Projected monthly cost** (daily cron, current cfg):
- Listing: $0.32 × 30 = **$9.6/mo**
- Comments: ~$0.45 × 30 = **~$13.5/mo**
- **Total ≈ $23/mo** — above the original $5 free-credit target, within Anna's $30/mo Apify
  spending cap. Cost decision was escalated separately; Anna chose to bind a card with $30/mo
  spend limit rather than reduce data volume.

## Schema mapping (harshmaur → our HotItem)

| HotItem field            | harshmaur post field                  |
|--------------------------|---------------------------------------|
| `id` / `source_native_id`| `parsedId` (or `id` minus `t3_`)      |
| `title`                  | `title`                               |
| `url`                    | `postUrl` (canonical Reddit URL)      |
| `author`                 | `authorName`                          |
| `published_at`           | `createdAt` (ISO)                     |
| `raw_metrics.likes`      | `upVotes`                             |
| `raw_metrics.comments`   | `commentsCount`                       |
| `raw_snippet`            | `body` (truncated)                    |
| `source_native.subreddit`| `parsedCommunityName`                 |
| `source_native.permalink`| derived from `postUrl` (strip host)   |
| `source_native.link_flair_text` | `flair`                        |
| stickied filter          | `stickied` (boolean)                  |

**Comment mapping** (same dataset, `dataType: "comment"`):

| canonical comment dict | harshmaur comment field |
|---|---|
| `body`     | `body` (HTML-stripped not needed; harshmaur returns plain text) |
| `score`    | `commentUpVotes`        |
| `author`   | `authorName`            |
| `is_op`    | computed via `comment.authorName == post.authorName` (harshmaur doesn't surface a per-comment `is_submitter`; we link comments to posts via `parsedPostId` / `postId` / `parentId` and check the post's `authorName`) |
| `replies`  | not surfaced — left at 0 |
| `id` / `fullname` | `parsedId` / `t1_<id>` |

## Why not the others

- **trudax/reddit-scraper-lite** — schema gap (`upVotes`/`numberOfComments` absent at the wire,
  not a clean=true artifact). `hot_score` cannot be computed.
- **fatihtahta/reddit-scraper-search-fast** — best schema and cheapest per-result, but the
  multi-target `urls` input fails the actor in ~10s. Per-sub serial runs would blow wall-clock
  past the 15-min workflow timeout.
- **trudax full + epctex + comchat** — gated behind paid rental or account-level grants.

## Implementation notes (for posterity, no live changes needed)

- `pipeline/sources/reddit_source.py` exposes two methods used by `pipeline/runner.py`:
  - `RedditSource.fetch()` → listing call, 1 Apify run, populates HotItems with empty
    `source_native["comments"]`
  - `RedditSource.fetch_comments_for_urls(urls, max_comments)` → comments call, 1 Apify run,
    returns `{permalink: [comment dicts]}` keyed by canonical Reddit path-only permalink. Also
    populates an in-memory cache so the legacy per-post `fetch_post_comments(permalink)` path
    serves from memory.
- `pipeline/runner.py::_enrich_top_with_comments` detects `fetch_comments_for_urls` and uses the
  batch path; falls back to per-post + rate-limit pacing for sources that only expose
  `fetch_post_comments` (e.g. the legacy `old_html` fallback).
- `pipeline/run_once.py::build_sources` defaults Reddit `auth_mode` to `apify`. Override to
  `old_html` via cfg if Anna ever wants to A/B against the old path on her self-hosted runner.
- `APIFY_TOKEN` is required in env when `auth_mode == "apify"`. Missing token → `RuntimeError`
  at first fetch (which surfaces as `failed_sources` in the run summary and a degraded exit 1).
