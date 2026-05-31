import { useCallback, useEffect, useMemo, useState } from "react";
import Head from "next/head";
import Sidebar from "@/components/Sidebar";
import RunTab from "@/components/RunTab";
import StarredTab from "@/components/StarredTab";
import HistoryTab from "@/components/HistoryTab";
import SettingsTab from "@/components/SettingsTab";
import { TopicLite } from "@/components/TopicPanel";
import { Report, ReportItem, RunSummary, TabKey } from "@/lib/types";
import { useT } from "@/lib/i18n";

export default function Home() {
  const { t } = useT();
  const [tab, setTab] = useState<TabKey>("run");
  const [report, setReport] = useState<Report | null>(null);
  const [scrapedSubreddits, setScrapedSubreddits] = useState<string[]>([]);
  const [activeTopic, setActiveTopic] = useState<string>("");
  const [topics, setTopics] = useState<TopicLite[]>([]);
  const [switching, setSwitching] = useState(false);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [starredItems, setStarredItems] = useState<ReportItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string>("");

  // Star library is keyed by stable post_id (accumulates across runs, doesn't change with the current report).
  const starredIds = useMemo(
    () => new Set(starredItems.map((i) => i.id)),
    [starredItems]
  );

  // runId not passed = latest; passed = that specific run (after a manual run, read the **returned**
  // run_id rather than falling back to latest). Returns the loaded Report (or null) so callers that
  // need the just-fetched payload don't have to wait a render cycle to read the React state.
  const loadReport = useCallback(async (runId?: number | string): Promise<Report | null> => {
    const url = runId ? `/api/run/${runId}` : "/api/run/latest";
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Report load failed (${res.status})`);
    const r = await res.json();
    const loaded: Report | null = r.report ?? null;
    setReport(loaded);
    setScrapedSubreddits(Array.isArray(r.scrapedSubreddits) ? r.scrapedSubreddits : []);
    return loaded;
  }, []);

  const loadStarred = useCallback(async () => {
    const res = await fetch("/api/starred");
    if (!res.ok) throw new Error(`Starred library load failed (${res.status})`);
    const r = await res.json();
    setStarredItems(Array.isArray(r.items) ? r.items : []);
  }, []);

  const loadRuns = useCallback(async () => {
    const res = await fetch("/api/runs?limit=100");
    if (!res.ok) throw new Error(`Run history load failed (${res.status})`);
    const r = await res.json();
    setRuns(Array.isArray(r.runs) ? r.runs : []);
  }, []);

  const loadTopics = useCallback(async () => {
    const res = await fetch("/api/topics");
    if (!res.ok) throw new Error(`Topics load failed (${res.status})`);
    const r = await res.json();
    const list: TopicLite[] = Array.isArray(r.topics) ? r.topics : [];
    setTopics(list);
    setActiveTopic(list.find((t) => t.status === "active")?.keyword ?? "");
  }, []);

  useEffect(() => {
    // On mount: fetch first-screen data; setState happens after await (async), not synchronously inside effect.
    (async () => {
      try {
        await Promise.all([loadReport(), loadStarred(), loadTopics(), loadRuns()]);
      } catch (e) {
        setLoadError((e as Error).message);
      } finally {
        setLoading(false);
      }
    })();
  }, [loadReport, loadStarred, loadTopics, loadRuns]);


  const toggle = useCallback(
    async (id: string) => {
      const isStarred = starredIds.has(id);
      await fetch("/api/star", {
        method: isStarred ? "DELETE" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ post_id: Number(id) }),
      });
      await loadStarred();
    },
    [starredIds, loadStarred]
  );

  // Pipeline now runs as a GitHub Actions workflow_run (Anna 2026-05-31, A plan): /api/run no longer
  // waits for completion — it dispatches the workflow and returns immediately. We poll
  // /api/runs/latest-id every POLL_MS until the active topic's latest run_id is strictly greater than
  // the baseline (captured before dispatch), then load that run's report. Hard timeout = POLL_TIMEOUT_MS;
  // beyond that we surface a "check GH Actions" message rather than spin forever.
  const runPipeline = useCallback(
    async (topic: string) => {
      const POLL_MS = 5000;
      const POLL_TIMEOUT_MS = 120_000;

      setRunning(true);
      setRunMsg(t("run.msg.running"));

      // Baseline: the active topic's current latest run_id (null = topic has no runs yet). Captured
      // BEFORE dispatch so a successful workflow_run always satisfies `newRunId > baseline`.
      //
      // CRITICAL (Rex Phase 1): distinguish "topic has no runs" (server returned {run_id: null})
      // from "couldn't reach server" (network / 5xx). If we collapse the latter to null, the
      // polling loop's `baselineRunId == null` branch would treat the *existing* latest run as
      // "new" on the first poll tick — the user gets shown a stale report and told "✅ done"
      // before the dispatched workflow has even started.
      let baselineRunId: number | null;
      try {
        const baseRes = await fetch("/api/runs/latest-id");
        if (!baseRes.ok) throw new Error(`baseline HTTP ${baseRes.status}`);
        const base = await baseRes.json();
        baselineRunId = typeof base?.run_id === "number" ? base.run_id : null;
      } catch (e) {
        setRunMsg(`${t("run.msg.failedPrefix")}${(e as Error).message}`);
        setRunning(false);
        return;
      }

      try {
        const dispatch = await fetch("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ topic }),
        }).then((res) => res.json());
        if (!dispatch.ok) {
          setRunMsg(
            `${t("run.msg.failedPrefix")}${dispatch.error || dispatch.message || t("run.msg.unknownError")}`,
          );
          setRunning(false);
          return;
        }

        const startedAt = Date.now();
        // Poll until a new run lands or we time out. Errors on a single poll tick are swallowed and
        // retried on the next tick — transient 5xx shouldn't abort the whole wait.
        for (;;) {
          if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
            setRunMsg(
              t("run.msg.timeoutTpl", {
                seconds: Math.round(POLL_TIMEOUT_MS / 1000),
              }),
            );
            setRunning(false);
            return;
          }
          await new Promise((r) => setTimeout(r, POLL_MS));
          try {
            const cur = await fetch("/api/runs/latest-id").then((r) => r.json());
            const newRunId: number | null =
              typeof cur?.run_id === "number" ? cur.run_id : null;
            if (newRunId != null && (baselineRunId == null || newRunId > baselineRunId)) {
              // loadReport's return value is the success signal (we need the items count for
              // doneTpl, and the new report is the user-visible outcome). loadRuns is a
              // best-effort sidecar — if it fails, the History tab will be one click behind
              // but the user's current report is correct. Don't let a History tab refresh
              // failure mask a successful pipeline run.
              let loaded: Report | null = null;
              try {
                loaded = await loadReport(newRunId);
              } catch (e) {
                setRunMsg(
                  `${t("run.msg.failedPrefix")}${(e as Error).message}`,
                );
                setRunning(false);
                return;
              }
              loadRuns().catch((e) =>
                console.warn("loadRuns refresh failed after run:", e),
              );
              setRunMsg(
                t("run.msg.doneTpl", { top: loaded?.items.length ?? "", failed: "" }),
              );
              setRunning(false);
              return;
            }
          } catch {
            // swallow & retry — see comment above
          }
        }
      } catch (e) {
        setRunMsg(`${t("run.msg.failedPrefix")}${(e as Error).message}`);
        setRunning(false);
      }
    },
    [loadReport, loadRuns, t]
  );

  const switchTopic = useCallback(
    async (keyword: string, hint?: string) => {
      setSwitching(true);
      setRunMsg("");
      try {
        const r = await fetch("/api/topics", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keyword, hint }),
        }).then((res) => res.json());
        if (r.ok || r.topic) {
          // Switched to a new topic: refresh topics/runs lists; load the new topic's latest report (likely none yet)
          await Promise.all([loadTopics(), loadRuns(), loadReport()]);
        } else {
          setRunMsg(`${t("run.msg.switchFailedPrefix")}${r.error || r.message || t("run.msg.unknownError")}`);
        }
      } catch (e) {
        setRunMsg(`${t("run.msg.switchFailedPrefix")}${(e as Error).message}`);
      } finally {
        setSwitching(false);
      }
    },
    [loadTopics, loadRuns, loadReport, t]
  );

  const deleteTopic = useCallback(
    async (topicId: number, keyword: string) => {
      try {
        const res = await fetch("/api/topics", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ topic_id: topicId }),
        });
        const r = await res.json();
        if (!res.ok || !r.ok) {
          // active/active and similar cases → surface a clear message to the user
          setRunMsg(t("run.msg.deleteFailedTpl", {
            kw: keyword,
            msg: r.message || r.error || t("run.msg.unknownError"),
          }));
          return;
        }
        setRunMsg(t("run.msg.deleteDoneTpl", { kw: keyword }));
        // If we're currently viewing this topic's history → main area would point at deleted data; reset to active latest
        const promises: Promise<unknown>[] = [loadTopics(), loadRuns()];
        if (report && report.topic === keyword) promises.push(loadReport());
        await Promise.all(promises);
      } catch (e) {
        setRunMsg(t("run.msg.deleteFailedTpl", { kw: keyword, msg: (e as Error).message }));
      }
    },
    [loadTopics, loadRuns, loadReport, report, t]
  );

  // "Back to current topic's latest": used in history-viewing mode to reset to the active topic's latest report.
  const backToActiveLatest = useCallback(async () => {
    try {
      await loadReport();
    } catch (e) {
      setRunMsg(`${t("run.msg.backFailedPrefix")}${(e as Error).message}`);
    }
  }, [loadReport, t]);

  return (
    <>
      <Head>
        <title>{t("app.title")}</title>
        <meta name="description" content={t("app.desc")} />
      </Head>
      <div className="flex min-h-screen flex-col md:flex-row">
        <Sidebar active={tab} onChange={setTab} starCount={starredItems.length} />
        <main className="max-w-5xl flex-1 px-6 py-7 md:px-8">
          {loading ? (
            <div className="py-16 text-center text-sm text-mut">{t("app.loading")}</div>
          ) : (
            <>
              {loadError && (
                <div className="mb-4 rounded-[10px] border border-[#e7b4a0] bg-[#fbe7df] px-3 py-2 text-xs text-[#a23b1d]">
                  {t("app.loadErrorTpl", { msg: loadError })}
                </div>
              )}
              {tab === "run" && (
                <RunTab
                  report={report}
                  scrapedSubreddits={scrapedSubreddits}
                  activeTopic={activeTopic}
                  topics={topics}
                  starred={starredIds}
                  onToggle={toggle}
                  onRun={runPipeline}
                  onSwitchTopic={switchTopic}
                  onDeleteTopic={deleteTopic}
                  onBackToActiveLatest={backToActiveLatest}
                  running={running}
                  switching={switching}
                  runMsg={runMsg}
                />
              )}
              {tab === "star" && (
                <StarredTab items={starredItems} starred={starredIds} onToggle={toggle} />
              )}
              {tab === "history" && (
                <HistoryTab runs={runs} starred={starredIds} onToggle={toggle} />
              )}
              {tab === "set" && <SettingsTab />}
            </>
          )}
        </main>
      </div>
    </>
  );
}
