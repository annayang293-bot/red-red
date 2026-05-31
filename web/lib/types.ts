export type ReportComment = {
  // Each entry mirrors what the Reddit comment parser produces (pipeline/sources/reddit_source.py).
  // System ② (drafting) will consume these directly as raw material for Xiaohongshu posts.
  id: string;
  author: string;
  score: number;
  body: string;        // Plain text, ≤800 chars; may end with "…(truncated)"
  is_op: boolean;      // True if the comment author == the original post's author
  replies: number;
};

export type ReportItem = {
  id: string;   // Stable identity (used for star / React key) = string form of posts_archive.post_id
  rank: number; // Sort position within this report only — NOT the identity
  title: string;
  tier_emoji: string; // 🔥 | 🟡 | ⚪
  tier_name: string;  // 强迁移 | 中等迁移 | 弱迁移
  tier_desc: string;
  source: string;
  english: string;
  likes: string;
  comments: string;
  url: string;
  comment: string;
  is_new?: boolean; // true = new this run; false = post recurring; undefined = no run context (e.g. starred library)
  comments_summary?: ReportComment[]; // Top-N comments from the original Reddit post (Anna 2026-05-31)
};

export type Report = {
  date: string;
  topic: string;
  items: ReportItem[];
};

export type TabKey = "run" | "star" | "history" | "set";

// Run history (from /api/runs) — for the report history dropdown
export type RunSummary = {
  run_id: number;
  topic_keyword: string;
  started_at: string;
  status: string;
  top20_count: number | null;
  ai_mode: string | null;
};

// Tier → color class (warm palette)
export const tierColor: Record<string, string> = {
  "🔥": "text-strong",
  "🟡": "text-mid",
  "⚪": "text-weak",
};
