import { useCallback, useEffect, useMemo, useState } from "react";
import Head from "next/head";
import Sidebar from "@/components/Sidebar";
import RunTab from "@/components/RunTab";
import StarredTab from "@/components/StarredTab";
import HistoryTab from "@/components/HistoryTab";
import SettingsTab from "@/components/SettingsTab";
import { TopicLite } from "@/components/TopicPanel";
import { Report, ReportItem, RunSummary, TabKey } from "@/lib/types";

export default function Home() {
  const [tab, setTab] = useState<TabKey>("run");
  const [report, setReport] = useState<Report | null>(null);
  const [activeTopic, setActiveTopic] = useState<string>("");
  const [topics, setTopics] = useState<TopicLite[]>([]);
  const [switching, setSwitching] = useState(false);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [starredItems, setStarredItems] = useState<ReportItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string>("");

  // 收藏以稳定 post_id 为键(跨 run 累积,不随当前报告变)
  const starredIds = useMemo(
    () => new Set(starredItems.map((i) => i.id)),
    [starredItems]
  );

  // runId 不传 = 最近一次;传了 = 指定那次(手动跑完要读**本次**返回的 run_id,别回拉 latest)
  const loadReport = useCallback(async (runId?: number | string) => {
    const url = runId ? `/api/run/${runId}` : "/api/run/latest";
    const res = await fetch(url);
    if (!res.ok) throw new Error(`报告加载失败(${res.status})`);
    const r = await res.json();
    setReport(r.report ?? null);
  }, []);

  const loadStarred = useCallback(async () => {
    const res = await fetch("/api/starred");
    if (!res.ok) throw new Error(`精选库加载失败(${res.status})`);
    const r = await res.json();
    setStarredItems(Array.isArray(r.items) ? r.items : []);
  }, []);

  const loadRuns = useCallback(async () => {
    const res = await fetch("/api/runs?limit=100");
    if (!res.ok) throw new Error(`运行历史加载失败(${res.status})`);
    const r = await res.json();
    setRuns(Array.isArray(r.runs) ? r.runs : []);
  }, []);

  const loadTopics = useCallback(async () => {
    const res = await fetch("/api/topics");
    if (!res.ok) throw new Error(`主题加载失败(${res.status})`);
    const r = await res.json();
    const list: TopicLite[] = Array.isArray(r.topics) ? r.topics : [];
    setTopics(list);
    setActiveTopic(list.find((t) => t.status === "active")?.keyword ?? "");
  }, []);

  useEffect(() => {
    // 挂载时拉首屏数据;setState 在 await 之后(异步),不在 effect 同步体里。
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

  // 历史 tab 点某次运行 → 加载到主区(loadReport 接 runId)
  const selectRun = useCallback(
    async (runId: number) => {
      try {
        await loadReport(runId);
      } catch (e) {
        setRunMsg(`加载失败:${(e as Error).message}`);
      }
    },
    [loadReport]
  );

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

  const runPipeline = useCallback(
    async (topic: string) => {
      setRunning(true);
      setRunMsg("正在跑(抓取 + 打分 + 点评,约 30–60 秒)…");
      try {
        const r = await fetch("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ topic }),
        }).then((res) => res.json());
        if (r.ok) {
          // 读本次 run 的报告 + 刷新历史列表(让新 run 出现在"历史"tab 里)
          await Promise.all([loadReport(r.run_id), loadRuns()]);
          const failed =
            Array.isArray(r.failed_sources) && r.failed_sources.length
              ? `(部分源没取到:${r.failed_sources.join("、")})`
              : "";
          setRunMsg(`✅ 跑完了:${r.top} 条进报告 ${failed}`);
        } else {
          setRunMsg(`没跑成:${r.error || r.message || "未知错误"}`);
        }
      } catch (e) {
        setRunMsg(`没跑成:${(e as Error).message}`);
      } finally {
        setRunning(false);
      }
    },
    [loadReport, loadRuns]
  );

  const switchTopic = useCallback(
    async (keyword: string) => {
      setSwitching(true);
      setRunMsg("");
      try {
        const r = await fetch("/api/topics", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ keyword }),
        }).then((res) => res.json());
        if (r.ok || r.topic) {
          // 切到新主题:刷新主题/运行列表;拉新主题的最新报告(多半还没有)
          await Promise.all([loadTopics(), loadRuns(), loadReport()]);
        } else {
          setRunMsg(`切换失败:${r.error || r.message || "未知错误"}`);
        }
      } catch (e) {
        setRunMsg(`切换失败:${(e as Error).message}`);
      } finally {
        setSwitching(false);
      }
    },
    [loadTopics, loadRuns, loadReport]
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
          // 有 active/active 等情况 → 给用户清晰提示
          setRunMsg(`没删成"${keyword}":${r.message || r.error || "未知"}`);
          return;
        }
        setRunMsg(`✅ 删了主题"${keyword}"`);
        // 若正看的是这个主题的历史报告 → 主区可能成"指向已删数据"的孤儿,reset 到 active latest
        const promises: Promise<unknown>[] = [loadTopics(), loadRuns()];
        if (report && report.topic === keyword) promises.push(loadReport());
        await Promise.all(promises);
      } catch (e) {
        setRunMsg(`没删成"${keyword}":${(e as Error).message}`);
      }
    },
    [loadTopics, loadRuns, loadReport, report]
  );

  // "回当前主题最新":历史查看模式下用,reset 回 active topic 的最新报告
  const backToActiveLatest = useCallback(async () => {
    try {
      await loadReport();
    } catch (e) {
      setRunMsg(`回退失败:${(e as Error).message}`);
    }
  }, [loadReport]);

  return (
    <>
      <Head>
        <title>系统① 热点选题</title>
        <meta name="description" content="给小红书选选题" />
      </Head>
      <div className="flex min-h-screen flex-col md:flex-row">
        <Sidebar active={tab} onChange={setTab} starCount={starredItems.length} />
        <main className="max-w-5xl flex-1 px-6 py-7 md:px-8">
          {loading ? (
            <div className="py-16 text-center text-sm text-mut">加载中…</div>
          ) : (
            <>
              {loadError && (
                <div className="mb-4 rounded-[10px] border border-[#e7b4a0] bg-[#fbe7df] px-3 py-2 text-xs text-[#a23b1d]">
                  ⚠️ 加载出错:{loadError}(检查后端 / 刷新重试)
                </div>
              )}
              {tab === "run" && (
                <RunTab
                  report={report}
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
                <HistoryTab
                  runs={runs}
                  onSelect={async (runId) => {
                    await selectRun(runId);   // 加载这次报告
                    setTab("run");             // 跳到"跑一次"页查看
                  }}
                />
              )}
              {tab === "set" && <SettingsTab />}
            </>
          )}
        </main>
      </div>
    </>
  );
}
