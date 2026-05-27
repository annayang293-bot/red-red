/**
 * DB 行 → 前端类型的映射 + 边界校验(收掉 Rex Step5 🟡「`as Report` → runtime 校验」)。
 *
 * 后端来的数据不硬断言成 Report,而是在这里逐行收口:校验必备字段、跳过坏行、
 * 把 posts_archive / report_top20 / starred 的形状统一成 ReportItem。
 */
import { Report, ReportItem } from "./types";

// report_top20.tier 是短名(强/中/弱);映射到前端展示三件套。
const TIER_META: Record<string, { emoji: string; name: string; desc: string }> = {
  强: { emoji: "🔥", name: "强迁移", desc: "直接能做小红书选题" },
  中: { emoji: "🟡", name: "中等迁移", desc: "要加工 / 看人设" },
  弱: { emoji: "⚪", name: "弱迁移", desc: "开发圈内 / 暂不建议" },
};
const TIER_UNKNOWN = { emoji: "⚪", name: "未分档", desc: "" };

// posts_archive 行(只取我们用到的列;PostgREST 返回的其余字段忽略)。
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
  run_id: number | null; // 首次入库那次 run(append-only)→ 判"新帖/老帖重现"
};

// report_top20 + 内联 posts_archive。comment / xhs_title 是 **per-run** 点评(随 run 变),
// 来自 report_top20 本行,不从 append-only 的 posts_archive.ai_review 读(Rex 🔴1)。
export type ReportRow = {
  rank: number;
  tier: string | null;
  comment: string | null;
  xhs_title: string | null;
  post_id: number;
  posts_archive: PostRow | null;
};

// starred + 内联 posts_archive
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

/** 来源展示名:reddit→r/版块、product_hunt→Product Hunt、其它→原值。 */
function sourceDisplay(post: PostRow): string {
  if (post.source === "reddit") {
    const sub = post.source_native?.["subreddit"];
    return sub ? `r/${toStr(sub)}` : "Reddit";
  }
  if (post.source === "product_hunt") return "Product Hunt";
  return post.source || "";
}

/**
 * 把一条 posts_archive(可带 rank/tier 覆盖)映射成 ReportItem。
 * 缺必备字段(post_id/title/url)→ 返回 null,由调用方跳过(边界校验)。
 */
export function postToReportItem(
  post: PostRow | null,
  opts: {
    rank?: number;
    tier?: string | null;
    comment?: string | null;
    xhs_title?: string | null;
    currentRunId?: number; // 传了就算"新帖(首见=本次)/老帖重现(首见在更早 run)"
  } = {}
): ReportItem | null {
  if (!post || post.post_id == null || !post.title || !post.url) return null;
  // 新帖 = 这条首次入库就是本次 run;老帖重现 = 之前 run 就见过(append-only:run_id=首见 run)
  const isNew =
    opts.currentRunId != null && post.run_id != null
      ? post.run_id === opts.currentRunId
      : undefined;
  // tier 优先用 report_top20.tier(短名);没有则回退 ai_review.tier(全名首字)。
  const shortTier =
    opts.tier ?? (post.ai_review?.tier ? post.ai_review.tier.slice(0, 1) : null);
  const meta = (shortTier && TIER_META[shortTier]) || TIER_UNKNOWN;
  const m = post.raw_metrics || {};
  // per-run 点评优先(report_top20);仅当没有(如精选库无 run 上下文)才回退首次快照 ai_review。
  const comment = opts.comment ?? toStr(post.ai_review?.comment);
  // 中文标题(真 LLM 才有);没有则用原标题(英文原文)。
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

/** report_top20(join posts_archive)行集 → ReportItem[](按 rank,跳过坏行)。
 *  currentRunId:本次报告的 run_id,用于标"新帖/老帖重现"。 */
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

/** starred(join posts_archive)行集 → ReportItem[](按收藏时间,跳过坏行)。 */
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

/** 组装一份 Report(给前端 RunTab/StarredTab 渲染)。 */
export function buildReport(date: string, topic: string, items: ReportItem[]): Report {
  return { date, topic, items };
}
