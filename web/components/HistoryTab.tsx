import { RunSummary } from "@/lib/types";

export default function HistoryTab({
  runs,
  onSelect,
}: {
  runs: RunSummary[];
  onSelect: (runId: number) => void;
}) {
  return (
    <div>
      <h1 className="text-xl font-bold">📅 历史</h1>
      <p className="mb-4 mt-0.5 text-[13px] text-mut">
        所有跑过的报告(按日期降序)。点一行 → 跳到那次报告查看。
      </p>

      {runs.length === 0 ? (
        <div className="py-10 text-center text-sm text-mut">还没有历史 —— 去&ldquo;跑一次&rdquo;</div>
      ) : (
        <div className="rounded-xl border border-line bg-panel">
          {runs.map((r, i) => {
            const date = (r.started_at || "").slice(0, 10);
            const time = (r.started_at || "").slice(11, 16);
            const isAi = r.ai_mode === "ai";
            return (
              <button
                key={r.run_id}
                onClick={() => onSelect(r.run_id)}
                className={
                  "flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition-colors hover:bg-terrasoft " +
                  (i < runs.length - 1 ? "border-b border-line" : "")
                }
              >
                <div className="w-24 shrink-0 font-semibold text-ink">
                  {date}
                  <div className="text-[10px] font-normal text-mut">{time}</div>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-ink">{r.topic_keyword}</div>
                  <div className="text-[11px] text-mut">
                    {r.top20_count ?? "-"} 条 · #{r.run_id}
                    {isAi ? " · AI 点评" : " · 占位点评"}
                  </div>
                </div>
                <span className="shrink-0 text-mut">›</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
