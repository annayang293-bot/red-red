/**
 * DB row → frontend type mapping + boundary validation (closes Rex Step 5 🟡 "`as Report` → runtime check").
 *
 * Backend data is NOT hard-cast to Report; instead it's funneled through here row by row:
 * required fields validated, bad rows skipped, and posts_archive / report_top20 / starred shapes
 * unified into ReportItem.
 */
import { Report, ReportComment, ReportItem } from "./types";

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
  // comments_summary lands here as JSONB from posts_archive — list of comment dicts written by
  // the runner's enrich step (only present for Top-N items at the time they entered a report).
  comments_summary: unknown;
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

/** Coerce a posts_archive.comments_summary JSONB blob into a typed list — defensively skip
 *  malformed entries instead of throwing (operator-data hygiene; the writer side could change). */
function parseComments(raw: unknown): ReportComment[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: ReportComment[] = [];
  for (const c of raw) {
    if (!c || typeof c !== "object") continue;
    const obj = c as Record<string, unknown>;
    const id = toStr(obj.id);
    const author = toStr(obj.author);
    const body = toStr(obj.body);
    if (!body) continue;  // Skip empties; nothing to show.
    out.push({
      id,
      author,
      score: typeof obj.score === "number" ? obj.score : Number(obj.score) || 0,
      body,
      is_op: Boolean(obj.is_op),
      replies: typeof obj.replies === "number" ? obj.replies : Number(obj.replies) || 0,
    });
  }
  return out.length > 0 ? out : undefined;
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
    // Set of post_ids that appeared in an EARLIER same-topic report_top20 (Anna 2026-05-28
    // semantics: "recurring" = "this post was already in a previous Top-20 report for this topic",
    // NOT "this post existed anywhere in posts_archive before now"). If undefined, no new/recurring
    // tagging is computed (e.g. the starred-library view has no run context).
    previouslyReportedPostIds?: Set<number>;
  } = {}
): ReportItem | null {
  if (!post || post.post_id == null || !post.title || !post.url) return null;
  // is_new = was this post in a previous Top-20? recurring if yes, new if no, undefined if we have no run context.
  const isNew =
    opts.previouslyReportedPostIds !== undefined
      ? !opts.previouslyReportedPostIds.has(post.post_id)
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
    comments_summary: parseComments(post.comments_summary),
  };
}

/** report_top20 (joined with posts_archive) row set → ReportItem[] (by rank, bad rows skipped).
 *  previouslyReportedPostIds: set of post_ids that appeared in earlier same-topic reports;
 *  callers compute this from runs+report_top20 and pass it in to tag "new / recurring". */
export function reportRowsToItems(rows: ReportRow[], previouslyReportedPostIds?: Set<number>): ReportItem[] {
  const items: ReportItem[] = [];
  for (const r of rows || []) {
    const it = postToReportItem(r.posts_archive, {
      rank: r.rank,
      tier: r.tier,
      comment: r.comment,
      xhs_title: r.xhs_title,
      previouslyReportedPostIds,
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
