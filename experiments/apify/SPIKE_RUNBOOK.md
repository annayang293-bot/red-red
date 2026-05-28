# Xiaohongshu Apify spike runbook (Richard 2026-05-24, line 2-A)

> Goal: verify whether "Xiaohongshu as a System ① topic source" is viable. Run a small job (10-20 results), fill in the 6 items below, hand back to Richard → he produces the long-term choice + cost model. **Don't burn the free tier in one go.**

## Setup
- Apify free account ($5/mo credits, no credit card required). **Waiting on Anna for the API token.**
- Primary actor: `zhorex/rednote-xiaohongshu-scraper` (free tier ~1000 results).
- Fallback: `easyapi` / `datapilot` rednote-search-scraper (if zhorex doesn't support keyword search).

## 6 verification items

**1｜Field coverage (most critical) — can it feed System ①'s hot_score (likes + comments + shares + time decay)**
Check each field in the actor's actual output JSON (✅ present / ❌ missing / ⚠ partial):
- [ ] Likes ⭐  [ ] Saves ⭐  [ ] Comments ⭐  [ ] Shares  [ ] Publish time ⭐ (required for time decay)
- [ ] Title  [ ] Body  [ ] Author  [ ] Topic tags  [ ] Image/video URLs  [ ] Note URL
- ⭐ Missing = fail the core requirement (becomes "PH RSS without vote counts" all over again).

**2｜Search mode — discover hot topics vs only monitor known accounts**
- [ ] Can you search by keyword/topic (try "AI" / "AI startup")? Or only by URL / user profile / note ID?
- [ ] Can you pull popular/explore feed?
- URL/user only = a "watch the top accounts" tool, not "discover hot topics by subject".

**3｜Rate / throttling**: max results per call, run duration, any throttling/failures.
**4｜Data freshness**: returned note publish time vs now — recent days realtime, or weeks-old data? Old = fail.
**5｜Stability**: run the same job 2-3 times, record success rate; check recent actor reviews + last-updated date.
**6｜Real cost (including hidden primary cost)**: from the Apify run console record per-call CU / proxy bandwidth consumption + billing ⭐ / any per-result fee → derive cost per 1000 results → project monthly cost at target volume.

## Pass / Fail
- **PASS**: engagement metrics + publish time + body complete + keyword search works + data recent + cost acceptable → move into long-term selection.
- **PARTIAL**: fields complete but only URL/user mode → "watch the top accounts" works, but "discover hot topics by subject" doesn't.
- **FAIL**: missing engagement / publish time, or stale data, or cost too high → try fallback actor; if that fails too → pivot to Browserbase / CU logged-in self-hosted.

## Execution result (2026-05-24, lil-Anna ran it on Anna's token)
actor=`zhorex/rednote-xiaohongshu-scraper`. Ran search "AI" 10 results + post_details 1 result.
- **Fields: two-step**. `search` (no cookie) only returns likes + postUrl + author + truncated title; `post_details` is the full payload (one verified note: likes 130 / saves 6 / comments 7 / shares 14 + publishedAt (epoch ms) + content (body + topic tags) + tags[3] + images[1]).
- **⚠️ Keyword search is imprecise**: searching "AI" returns unrelated content like "seal drinking iced coffee healing comic" (exact match is login-gated). `user_posts` (by account) is precise.
- Rate: maxResults ≤ 500; search of 10 ~30s. Freshness: publishedAt usable (post_details). Stability: 2 runs / 0 failures (warning about an unknown `post-scraped` billing event). Cost: run compute ~$0.0002; doc ~$0.005/result; two-step ~2x.
- **Verdict: PARTIAL**. Direction choice for Anna: A keyword discovery (needs login cookie) / B monitor benchmark accounts (user_posts, no login required, next step worth trying). Awaiting Anna's direction + Richard's long-term call.
