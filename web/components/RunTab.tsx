import { Report } from "@/lib/types";
import ReportList from "./ReportList";
import TopicPanel, { TopicLite } from "./TopicPanel";

export default function RunTab({
  report,
  activeTopic,
  topics,
  starred,
  onToggle,
  onRun,
  onSwitchTopic,
  onDeleteTopic,
  onBackToActiveLatest,
  running,
  switching,
  runMsg,
}: {
  report: Report | null;
  activeTopic: string;
  topics: TopicLite[];
  starred: Set<string>;
  onToggle: (id: string) => void;
  onRun: (topic: string) => void;
  onSwitchTopic: (keyword: string) => void;
  onDeleteTopic: (topicId: number, keyword: string) => void;
  onBackToActiveLatest: () => void;
  running: boolean;
  switching: boolean;
  runMsg: string;
}) {
  // 本次报告内容来自哪些来源(版块 / PH)——给右侧栏显示
  const sources = report
    ? Array.from(new Set(report.items.map((it) => it.source).filter(Boolean)))
    : [];
  // 新帖 / 老帖重现 计数(加固#2)
  const repeatCount = report ? report.items.filter((it) => it.is_new === false).length : 0;
  const newCount = report ? report.items.filter((it) => it.is_new === true).length : 0;
  const freshNote = report && (newCount || repeatCount) ? ` · ${newCount} 新 / ${repeatCount} 重现` : "";

  // 历史查看模式:正在看的报告不是当前 active 主题的报告(Rex Step7 🔴)。
  // 这种状态下要把"跑当前主题"的按钮收掉,免得用户在看 A 的历史时误触发 跑 B。
  const isViewingHistory = !!report && !!activeTopic && report.topic !== activeTopic;

  return (
    <div className="flex flex-col gap-5 md:flex-row md:items-start">
      {/* 主区:跑一次 + 报告 */}
      <div className="min-w-0 flex-1">
        <h1 className="text-xl font-bold">{isViewingHistory ? "📜 历史报告" : "🚀 跑一次"}</h1>
        <p className="mb-4 mt-0.5 text-[13px] text-mut">
          {report
            ? `${report.date} · 主题 ${report.topic} · 共 ${report.items.length} 条${freshNote} · 按选题潜力分档`
            : "还没有报告 —— 点“开始跑”出第一份"}
        </p>

        {isViewingHistory ? (
          // 看历史模式:不显示"开始跑"按钮(否则会跑成当前 active 主题,而不是眼前这份报告的主题)
          <div className="flex items-center gap-2.5 rounded-[10px] border border-[#e7b4a0] bg-[#fbe7df] px-3.5 py-2.5 text-sm text-[#a23b1d]">
            <span>
              📜 你在看历史 · 当前主题是 <b>{activeTopic}</b>,不是 <b>{report?.topic}</b>。
            </span>
            <button
              onClick={onBackToActiveLatest}
              className="ml-auto shrink-0 rounded-lg bg-terra px-3 py-1 text-xs font-semibold text-white"
            >
              回 {activeTopic} 最新 →
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2.5">
            <div className="flex-1 rounded-[10px] border border-line bg-panel px-3.5 py-2.5 text-sm text-mut">
              跑当前主题:<span className="font-semibold text-ink">{activeTopic || "(还没设置)"}</span>
            </div>
            <button
              onClick={() => activeTopic && onRun(activeTopic)}
              disabled={running || switching || !activeTopic}
              className="rounded-[10px] bg-terra px-5 font-semibold text-white disabled:opacity-50"
            >
              {running ? "跑着…" : "开始跑"}
            </button>
          </div>
        )}

        {runMsg && <p className="mb-1 mt-2 text-xs text-mut">{runMsg}</p>}

        <p className="mb-2 mt-1.5 text-xs text-mut">
          点标题左侧 ☆ 收藏到精选库 · 点标题打开原帖 · 想看历史报告去&ldquo;📅 历史&rdquo;
        </p>

        {report && report.items.length > 0 ? (
          <ReportList items={report.items} starred={starred} onToggle={onToggle} />
        ) : (
          !running && (
            <div className="py-10 text-center text-sm text-mut">
              {report ? "这次没有符合条件的内容" : "点上面的“开始跑”出第一份报告"}
            </div>
          )
        )}
      </div>

      {/* 右侧栏:主题管理 —— 永远展示当前 active 主题(跟主区"历史"无关) */}
      <TopicPanel
        topics={topics}
        activeTopic={activeTopic}
        sources={isViewingHistory ? [] : sources}
        onSwitch={onSwitchTopic}
        onDelete={onDeleteTopic}
        switching={switching}
      />
    </div>
  );
}
