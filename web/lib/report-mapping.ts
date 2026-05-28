/**
 * DB row → frontend type mapping + boundary validation (closes Rex Step 5 🟡 "`as Report` → runtime check").
 *
 * Backend data is NOT hard-cast to Report; instead it's funneled through here row by row:
 * required fields validated, bad rows skipped, and posts_archive / report_top20 / starred shapes
 * unified into ReportItem.
 */
import { Report, ReportItem } from "./types";

// report_top20.tier is short-name (强/中/弱); map to the frontend's three-piece display tuple.
const TIER_META: Record<string, { emoji: string; name: string; desc: string }> = {
  强: { emoji: "🔥", name: "强迁移", desc: "直接能做小红书选题" },
  中: { emoji: "🟡", name: "中等迁移", desc: "要加工 / 看人设" },
  弱: { emoji: "⚪", name: "弱迁移", desc: "开发圈内 / 暂不建议" },
};
const TIER_UNKNOWN = { emoji: "⚪", name: "未分档", desc: "" };

// posts_archive row (only columns we use; PostgREST returns more, others ignored).
export type PostRow = {
  post_id: number;
  source: string;
  source_native_id: string;
  title: string | null;
  url: string | null;
  raw_snippet: string | null;
  raw_metrics: Record<string, unknown> | null;
  ai_review: { tier?: string; comment?: string } | null;
  source_native: Record<string, unknown> | null;
  run_id: number | null; // first-insert run (append-only) → used to judge "new post / post recurring"
};

// report_top20 + inline posts_archive. comment / xhs_title are **per-run** review (changes run to run),
// taken from this report_top20 row, not from the append-only posts_archive.ai_review (Rex 🔴1).
export type ReportRow = {
  rank: number;
  tier: string | null;
  comment: string | null;
  xhs_title: string | null;
  post_id: number;
  posts_archive: PostRow | null;
};

// starred + inline posts_archive
export type StarredRow = {
  star_id: number;
  post_id: number;
  starred_at: string | null;
  posts_archive: PostRow | null;
};

function toStr(v: unknown): string {
  if (v === null || v === undefined) return "";
  return String(v);
}

/** Source display name: reddit → r/<sub>, product_hunt → "Product Hunt", others → raw value. */
function sourceDisplay(post: PostRow): string {
  if (post.source === "reddit") {
    const sub = post.source_native?.["subreddit"];
    return sub ? `r/${toStr(sub)}` : "Reddit";
  }
  if (post.source === "product_hunt") return "Product Hunt";
  return post.source || "";
}

/**
 * Map one posts_archive row (optionally with rank/tier overrides) to ReportItem.
 * Missing required fields (post_id / title / url) → returns null; callers skip (boundary validation).
 */
export function postToReportItem(
  post: PostRow | null,
  opts: {
    rank?: number;
    tier?: string | null;
    comment?: string | null;
    xhs_title?: string | null;
    currentRunId?: number; // If passed, computes "new (first-seen=this run) / recurring (first-seen earlier)"
  } = {}
): ReportItem | null {
  if (!post || post.post_id == null || !post.title || !post.url) return null;
  // New = this row's first-insert IS the current run; recurring = seen in an earlier run (append-only: run_id = first-seen run)
  const isNew =
    opts.currentRunId != null && post.run_id != null
      ? post.run_id === opts.currentRunId
      : undefined;
  // tier preference: report_top20.tier (short name); otherwise fall back to first char of ai_review.tier (full name).
  const shortTier =
    opts.tier ?? (post.ai_review?.tier ? post.ai_review.tier.slice(0, 1) : null);
  const meta = (shortTier && TIER_META[shortTier]) || TIER_UNKNOWN;
  const m = post.raw_metrics || {};
  // Prefer per-run comment (report_top20); only fall back to the first-seen snapshot ai_review when
  // there's no run context (e.g. starred library).
  const comment = opts.comment ?? toStr(post.ai_review?.comment);
  // Chinese title (only present for real LLM); if absent, fall back to the original (English) title.
  const title = opts.xhs_title || toStr(post.title);
  return {
    id: String(post.post_id),
    rank: opts.rank ?? 0,
    title,
    tier_emoji: meta.emoji,
    tier_name: meta.name,
    tier_desc: meta.desc,
    source: sourceDisplay(post),
    english: toStr(post.raw_snippet),
    likes: toStr(m["likes"]),
    comments: toStr(m["comments"]),
    url: toStr(post.url),
    comment: toStr(comment),
    is_new: isNew,
  };
}

/** report_top20 (joined with posts_archive) row set → ReportItem[] (by rank, bad rows skipped).
 *  currentRunId: this report's run_id, used to tag "new / recurring". */
export function reportRowsToItems(rows: ReportRow[], currentRunId?: number): ReportItem[] {
  const items: ReportItem[] = [];
  for (const r of rows || []) {
    const it = postToReportItem(r.posts_archive, {
      rank: r.rank,
      tier: r.tier,
      comment: r.comment,
      xhs_title: r.xhs_title,
      currentRunId,
    });
    if (it) items.push(it);
  }
  return items;
}

/** starred (joined with posts_archive) row set → ReportItem[] (by star time, bad rows skipped). */
export function starredRowsToItems(rows: StarredRow[]): ReportItem[] {
  const items: ReportItem[] = [];
  let rank = 1;
  for (const r of rows || []) {
    const it = postToReportItem(r.posts_archive, { rank: rank });
    if (it) {
      items.push(it);
      rank += 1;
    }
  }
  return items;
}

/** Assemble a Report (for RunTab / StarredTab rendering). */
export function buildReport(date: string, topic: string, items: ReportItem[]): Report {
  return { date, topic, items };
}
