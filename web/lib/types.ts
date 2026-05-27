export type ReportItem = {
  id: string;   // 稳定身份(收藏/React key 用)= posts_archive.post_id 的字符串形式
  rank: number; // 仅本次报告里的排序位置,不是身份
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
  is_new?: boolean; // true=本次新帖;false=老帖重现;undefined=无 run 上下文(如精选库)
};

export type Report = {
  date: string;
  topic: string;
  items: ReportItem[];
};

export type TabKey = "run" | "star" | "history" | "set";

// 运行历史(来自 /api/runs)—— 用于报告历史下拉
export type RunSummary = {
  run_id: number;
  topic_keyword: string;
  started_at: string;
  status: string;
  top20_count: number | null;
  ai_mode: string | null;
};

// 档 → 颜色 class(暖色调)
export const tierColor: Record<string, string> = {
  "🔥": "text-strong",
  "🟡": "text-mid",
  "⚪": "text-weak",
};
