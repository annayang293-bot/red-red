/**
 * Tiny i18n layer with a 中/英 toggle.
 *
 * - Persists user's choice in localStorage under `lang` ('zh' | 'en').
 * - Defaults to 'zh' to match Anna's day-to-day usage; 'en' switch covers the demo / external-share path.
 * - Two-dict design (zh / en) — no remote loading, no i18next dependency.
 * - Tier short names ("强" / "中" / "弱") stay as-is (DB tier-data values); only the human label
 *   strings go through the dictionary so callers can swap to English on toggle.
 */
import { createContext, useContext, useEffect, useState, useCallback } from "react";

export type Lang = "zh" | "en";
const DEFAULT_LANG: Lang = "zh";
const STORAGE_KEY = "system1.lang";

type Dict = Record<string, string>;
type Strings = Record<Lang, Dict>;

export const strings: Strings = {
  zh: {
    // App-level
    "app.title": "系统① 热点选题",
    "app.desc": "给小红书选选题",
    "app.loading": "加载中…",
    "app.loadErrorTpl": "⚠️ 加载出错:{msg}(检查后端 / 刷新重试)",

    // Sidebar
    "side.brand": "🔥 热点选题",
    "side.tag": "系统① · 给小红书选选题",
    "side.tab.run": "跑一次",
    "side.tab.star": "精选库",
    "side.tab.history": "历史",
    "side.tab.set": "设置",
    "side.langLabel": "界面",
    "side.langZh": "中",
    "side.langEn": "EN",

    // RunTab
    "run.heading.run": "🚀 跑一次",
    "run.heading.history": "📜 历史报告",
    "run.subtitleTpl": "{date} · 主题 {topic} · 共 {n} 条{fresh} · 按选题潜力分档",
    "run.subtitleFreshTpl": " · {newN} 新 / {repeatN} 重现",
    "run.subtitleEmpty": "还没有报告 —— 点“开始跑”出第一份",
    "run.viewingHistoryTpl": "📜 你在看历史 · 当前主题是 {active},不是 {viewing}。",
    "run.backToActiveTpl": "回 {active} 最新 →",
    "run.currentTopicLabel": "跑当前主题:",
    "run.currentTopicEmpty": "(还没设置)",
    "run.runBtnLabel": "开始跑",
    "run.runBtnRunning": "跑着…",
    "run.helper": "点标题左侧 ☆ 收藏到精选库 · 点标题打开原帖 · 想看历史报告去 “📅 历史”",
    "run.emptyResult.thisRun": "这次没有符合条件的内容",
    "run.emptyResult.firstRun": "点上面的 “开始跑” 出第一份报告",
    "run.msg.running": "正在跑(GitHub Actions 抓取 + 打分 + 点评,约 60–90 秒)…",
    "run.msg.failedPrefix": "没跑成:",
    "run.msg.unknownError": "未知错误",
    "run.msg.doneTpl": "✅ 跑完了:{top} 条进报告{failed}",
    "run.msg.failedSourcesTpl": "(部分源没取到:{srcs})",
    "run.msg.timeoutTpl": "等了 {seconds} 秒还没出新报告,可去 GitHub Actions 看日志。",
    "run.msg.switchFailedPrefix": "切换失败:",
    "run.msg.deleteFailedTpl": "没删成“{kw}”:{msg}",
    "run.msg.deleteDoneTpl": "✅ 删了主题“{kw}”",
    "run.msg.backFailedPrefix": "回退失败:",
    "run.progress.fetch": "约 1/4 步:从 Reddit / Product Hunt 抓取…",
    "run.progress.score": "约 2/4 步:打分排序…",
    "run.progress.review": "约 3/4 步:AI 点评分档…",
    "run.progress.save": "约 4/4 步:存入数据库…",

    // ReportList
    "list.openOriginalAria": "打开原帖",
    "list.starTitle": "收藏到精选库",
    "list.unstarTitle": "取消收藏",
    "list.recurring": "🔁 老帖重现",
    "list.product_hunt": "Product Hunt",
    "list.showCommentsTpl": "💬 看 {n} 条热评",
    "list.hideComments": "💬 收起热评",

    // StarredTab
    "star.heading": "⭐ 精选库",
    "star.subtitle": "你收藏过的选题(跨多次跑累积,已保存)",
    "star.all": "全部",
    "star.empty": "还没有收藏 —— 去 “跑一次” 点标题左侧的 ☆",
    "star.emptyForFilter": "这个来源还没收藏(换一个筛选或选 “全部”)",

    // HistoryTab
    "history.heading": "📅 历史",
    "history.subtitle": "所有跑过的报告(按日期降序)。点一行 → 在这里展开那次报告。",
    "history.empty": "还没有历史 —— 去 “跑一次”",
    "history.back": "← 返回历史列表",
    "history.detailSubtitleTpl": "{date} · 共 {n} 条 · 按选题潜力分档",
    "history.emptyResult": "这次没有符合条件的内容",
    "history.noData": "没有数据",
    "history.loadFailedTpl": "加载失败({status})",
    "history.aiBadge": "AI 点评",
    "history.heuristicBadge": "占位点评",
    "history.itemsCountTpl": "{n} 条 · #{id}",

    // SettingsTab
    "set.heading": "⚙️ 设置",
    "set.subtitle": "连接账号 / 关注词 / 排序口味",
    "set.hotnessNote": "热度怎么排:看点赞 + 评论 + 转发,越新的越靠前(当前是默认口味,以后可调)",
    "set.placeholder": "真功能(密钥 / 关注词 / 口味调节)做好后接到这里",

    // Tier names + descriptions (used by ReportList; switchable via toggle)
    "tier.strong.name": "强迁移",
    "tier.strong.desc": "直接能做小红书选题",
    "tier.mid.name": "中等迁移",
    "tier.mid.desc": "要加工 / 看人设",
    "tier.weak.name": "弱迁移",
    "tier.weak.desc": "开发圈内 / 暂不建议",
    "tier.unknown.name": "未分档",
    "tier.unknown.desc": "",

    // TopicPanel
    "topic.heading": "🎯 主题",
    "topic.activeBadge": "当前",
    "topic.scrapedAll": "AI 选了这些版块抓(全):",
    "topic.inReport": "这次报告里出现的:",
    "topic.others": "切到别的主题:",
    "topic.deleteTitleTpl": "删除主题 “{kw}”(连同它的所有历史报告)",
    "topic.newLabel": "新主题:",
    "topic.newPlaceholder": "如:具身智能 / AI 编程",
    "topic.newBtn": "建 + 切到它",
    "topic.switching": "切换中…",
    "topic.footer": "一次只跑一个主题。换主题 = 旧的归档、新的启用,各自的报告/收藏历史分开存,不会混。",
    "topic.addHintLink": "+ 额外提示给 AI(可选)",
    "topic.hintLabel": "额外提示词:",
    "topic.hintPlaceholder": "例:重点 indie SaaS,不要游戏开发",
    "topic.hintHelp": "AI 选版块时会先看这条;留空就用默认逻辑",
  },
  en: {
    // App-level
    "app.title": "System ① · Topic discovery",
    "app.desc": "Pick Xiaohongshu topics",
    "app.loading": "Loading…",
    "app.loadErrorTpl": "⚠️ Load error: {msg} (check backend / refresh)",

    // Sidebar
    "side.brand": "🔥 Topic discovery",
    "side.tag": "System ① · Pick Xiaohongshu topics",
    "side.tab.run": "Run",
    "side.tab.star": "Starred",
    "side.tab.history": "History",
    "side.tab.set": "Settings",
    "side.langLabel": "Lang",
    "side.langZh": "中",
    "side.langEn": "EN",

    // RunTab
    "run.heading.run": "🚀 Run",
    "run.heading.history": "📜 Historical report",
    "run.subtitleTpl": "{date} · Topic {topic} · {n} items{fresh} · tiered by topic potential",
    "run.subtitleFreshTpl": " · {newN} new / {repeatN} recurring",
    "run.subtitleEmpty": "No report yet — click \"Run\" to fetch the first one",
    "run.viewingHistoryTpl": "📜 You're viewing history · Active topic is {active}, not {viewing}.",
    "run.backToActiveTpl": "Back to latest of {active} →",
    "run.currentTopicLabel": "Run current topic:",
    "run.currentTopicEmpty": "(not set)",
    "run.runBtnLabel": "Run",
    "run.runBtnRunning": "Running…",
    "run.helper": "Click ☆ next to the title to star · Click the title to open the original · For history, see \"📅 History\"",
    "run.emptyResult.thisRun": "Nothing matched this run",
    "run.emptyResult.firstRun": "Click \"Run\" above to produce the first report",
    "run.msg.running": "Running on GitHub Actions (fetch + score + review, ~60–90s)…",
    "run.msg.failedPrefix": "Run failed: ",
    "run.msg.unknownError": "Unknown error",
    "run.msg.doneTpl": "✅ Done: {top} items in the report{failed}",
    "run.msg.failedSourcesTpl": " (some sources failed: {srcs})",
    "run.msg.timeoutTpl": "Waited {seconds}s with no new report — check the GitHub Actions log.",
    "run.msg.switchFailedPrefix": "Switch failed: ",
    "run.msg.deleteFailedTpl": "Couldn't delete \"{kw}\": {msg}",
    "run.msg.deleteDoneTpl": "✅ Deleted topic \"{kw}\"",
    "run.msg.backFailedPrefix": "Back failed: ",
    "run.progress.fetch": "~step 1/4: fetching from Reddit / Product Hunt…",
    "run.progress.score": "~step 2/4: scoring & ranking…",
    "run.progress.review": "~step 3/4: AI review & tiering…",
    "run.progress.save": "~step 4/4: saving to database…",

    // ReportList
    "list.openOriginalAria": "Open original",
    "list.starTitle": "Star to library",
    "list.unstarTitle": "Unstar",
    "list.recurring": "🔁 Seen before",
    "list.product_hunt": "Product Hunt",
    "list.showCommentsTpl": "💬 show {n} hot comments",
    "list.hideComments": "💬 hide comments",

    // StarredTab
    "star.heading": "⭐ Starred library",
    "star.subtitle": "Topics you've starred, accumulated across runs (persisted).",
    "star.all": "All",
    "star.empty": "Nothing starred yet — head to \"Run\" and click ☆ next to a title",
    "star.emptyForFilter": "No starred items for this source (pick another filter or \"All\")",

    // HistoryTab
    "history.heading": "📅 History",
    "history.subtitle": "All previous runs (newest first). Click a row → expand that report inline.",
    "history.empty": "No history yet — head to \"Run\"",
    "history.back": "← Back to history list",
    "history.detailSubtitleTpl": "{date} · {n} items · tiered by topic potential",
    "history.emptyResult": "Nothing matched this run",
    "history.noData": "No data",
    "history.loadFailedTpl": "Load failed ({status})",
    "history.aiBadge": "AI review",
    "history.heuristicBadge": "heuristic",
    "history.itemsCountTpl": "{n} items · #{id}",

    // SettingsTab
    "set.heading": "⚙️ Settings",
    "set.subtitle": "Account links / keyword list / scoring taste",
    "set.hotnessNote": "Hotness ranking uses likes + comments + saves; newer ranks higher (default taste, tunable later).",
    "set.placeholder": "Real settings (keys / keywords / scoring knobs) will surface here once shipped",

    // Tier names + descriptions
    "tier.strong.name": "Strong",
    "tier.strong.desc": "Directly usable as a Xiaohongshu topic",
    "tier.mid.name": "Medium",
    "tier.mid.desc": "Needs rework / depends on persona",
    "tier.weak.name": "Weak",
    "tier.weak.desc": "Niche / not recommended",
    "tier.unknown.name": "Untiered",
    "tier.unknown.desc": "",

    // TopicPanel
    "topic.heading": "🎯 Topics",
    "topic.activeBadge": "active",
    "topic.scrapedAll": "AI picked these subreddits (all):",
    "topic.inReport": "Subreddits in this report:",
    "topic.others": "Switch to another topic:",
    "topic.deleteTitleTpl": "Delete topic \"{kw}\" (along with all its history)",
    "topic.newLabel": "New topic:",
    "topic.newPlaceholder": "e.g. embodied AI / AI coding",
    "topic.newBtn": "Create + switch",
    "topic.switching": "Switching…",
    "topic.footer": "One topic at a time. Switching = old one archived, new one activated; each keeps its own report/star history, never mixed.",
    "topic.addHintLink": "+ Extra hint for the AI (optional)",
    "topic.hintLabel": "Extra hint:",
    "topic.hintPlaceholder": "e.g. focus on indie SaaS, NOT game dev",
    "topic.hintHelp": "AI reads this first when picking subreddits; leave empty for default logic",
  },
};

export function format(s: string, vars?: Record<string, string | number>): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (_, k) => (k in vars ? String(vars[k]) : `{${k}}`));
}

export function t(lang: Lang, key: string, vars?: Record<string, string | number>): string {
  const dict = strings[lang] || strings[DEFAULT_LANG];
  const raw = dict[key] ?? strings[DEFAULT_LANG][key] ?? key;
  return format(raw, vars);
}

// React context so callers don't need to thread `lang` through every prop.
export const LangContext = createContext<{
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}>({
  lang: DEFAULT_LANG,
  setLang: () => undefined,
  t: (k) => k,
});

export function useLangState() {
  const [lang, setLangState] = useState<Lang>(DEFAULT_LANG);
  // Sync from localStorage after mount: SSR and first client render both use DEFAULT_LANG so the
  // hydrated DOM matches; then this effect upgrades to the user's saved choice. The lint rule warns
  // against setState in an effect, but this is the canonical pattern for "read persisted-client state
  // after hydration" — there's no external system to subscribe to.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored === "zh" || stored === "en") setLangState(stored);
  }, []);
  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    if (typeof window !== "undefined") window.localStorage.setItem(STORAGE_KEY, l);
  }, []);
  const tFn = useCallback(
    (key: string, vars?: Record<string, string | number>) => t(lang, key, vars),
    [lang]
  );
  return { lang, setLang, t: tFn };
}

export function useT() {
  return useContext(LangContext);
}
