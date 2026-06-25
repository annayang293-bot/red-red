import { useState } from "react";
import { Report, RunSummary } from "@/lib/types";
import ReportList from "./ReportList";
import { useT } from "@/lib/i18n";
import { authedFetch } from "@/lib/authed-fetch";

export default function HistoryTab({
  runs,
  starred,
  onToggle,
}: {
  runs: RunSummary[];
  starred: Set<string>;
  onToggle: (id: string) => void;
}) {
  const { t } = useT();
  // Click a run → expand that report inline within this tab (Anna 2026-05-27: don't jump to "Run").
  const [openRunId, setOpenRunId] = useState<number | null>(null);
  const [openReport, setOpenReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const open = async (runId: number) => {
    setOpenRunId(runId);
    setOpenReport(null);
    setErr("");
    setLoading(true);
    try {
      const res = await authedFetch(`/api/run/${runId}`);
      if (!res.ok) throw new Error(t("history.loadFailedTpl", { status: res.status }));
      const r = await res.json();
      setOpenReport(r.report ?? null);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const back = () => {
    setOpenRunId(null);
    setOpenReport(null);
    setErr("");
  };

  // --- Detail mode: a specific report is expanded ---
  if (openRunId !== null) {
    return (
      <div>
        <button
          onClick={back}
          className="mb-3 text-sm text-mut transition-colors hover:text-terra"
        >
          {t("history.back")}
        </button>
        {loading ? (
          <div className="py-10 text-center text-sm text-mut">{t("app.loading")}</div>
        ) : err ? (
          <div className="rounded-[10px] border border-[#e7b4a0] bg-[#fbe7df] px-3 py-2 text-sm text-[#a23b1d]">
            ⚠️ {err}
          </div>
        ) : openReport ? (
          <>
            <h1 className="text-xl font-bold">📜 {openReport.topic}</h1>
            <p className="mb-4 mt-0.5 text-[13px] text-mut">
              {t("history.detailSubtitleTpl", {
                date: openReport.date,
                n: openReport.items.length,
              })}
            </p>
            {openReport.items.length > 0 ? (
              <ReportList items={openReport.items} starred={starred} onToggle={onToggle} />
            ) : (
              <div className="py-10 text-center text-sm text-mut">
                {t("history.emptyResult")}
              </div>
            )}
          </>
        ) : (
          <div className="py-10 text-center text-sm text-mut">{t("history.noData")}</div>
        )}
      </div>
    );
  }

  // --- List mode: all runs in descending date order ---
  return (
    <div>
      <h1 className="text-xl font-bold">{t("history.heading")}</h1>
      <p className="mb-4 mt-0.5 text-[13px] text-mut">{t("history.subtitle")}</p>

      {runs.length === 0 ? (
        <div className="py-10 text-center text-sm text-mut">{t("history.empty")}</div>
      ) : (
        <div className="rounded-xl border border-line bg-panel">
          {runs.map((r, i) => {
            // `r.started_at` is an ISO timestamp with Z/+00:00 offset (Supabase serializes
            // timestamptz that way). Slicing it directly would surface UTC literals to the
            // user — Anna 2026-06-01 caught a Run-54 row that read "2026-06-01 00:22" when she
            // actually clicked Run on 5/31 17:22 PDT. Going through `new Date()` lets the
            // browser convert to whatever locale/tz the user's system reports.
            // The runs list only renders client-side (gated behind the `loading` flag in
            // pages/index.tsx, which awaits a client useEffect fetch), so there's no SSR
            // hydration mismatch from this.
            const ts = r.started_at ? new Date(r.started_at) : null;
            const date = ts ? ts.toLocaleDateString() : "";
            const time = ts ? ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
            const isAi = r.ai_mode === "ai";
            return (
              <button
                key={r.run_id}
                onClick={() => open(r.run_id)}
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
                    {t("history.itemsCountTpl", { n: r.top20_count ?? "-", id: r.run_id })}
                    {" · "}
                    {isAi ? t("history.aiBadge") : t("history.heuristicBadge")}
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
