import { Report } from "@/lib/types";
import ReportList from "./ReportList";
import TopicPanel, { TopicLite } from "./TopicPanel";
import RunProgress from "./RunProgress";
import { useT } from "@/lib/i18n";

export default function RunTab({
  report,
  scrapedSubreddits,
  activeTopic,
  topics,
  starred,
  onToggle,
  onRun,
  onSwitchTopic,
  onDeleteTopic,
  onToggleAutoDaily,
  onBackToActiveLatest,
  running,
  switching,
  runMsg,
}: {
  report: Report | null;
  scrapedSubreddits: string[];
  activeTopic: string;
  topics: TopicLite[];
  starred: Set<string>;
  onToggle: (id: string) => void;
  onRun: (topic: string) => void;
  onSwitchTopic: (keyword: string, hint?: string) => void;
  onDeleteTopic: (topicId: number, keyword: string) => void;
  onToggleAutoDaily: (topicId: number, next: boolean) => void;
  onBackToActiveLatest: () => void;
  running: boolean;
  switching: boolean;
  runMsg: string;
}) {
  const { t } = useT();
  // Which sources (subreddits / PH) this report's items came from — passed to the right column.
  const sources = report
    ? Array.from(new Set(report.items.map((it) => it.source).filter(Boolean)))
    : [];
  // New / recurring counts (hardening #2)
  const repeatCount = report ? report.items.filter((it) => it.is_new === false).length : 0;
  const newCount = report ? report.items.filter((it) => it.is_new === true).length : 0;
  const freshNote =
    report && (newCount || repeatCount)
      ? t("run.subtitleFreshTpl", { newN: newCount, repeatN: repeatCount })
      : "";

  // History viewing mode: the report being viewed isn't the current active topic's report (Rex Step 7 🔴).
  // In this state we hide the "run current topic" button to prevent accidentally running topic B
  // while viewing topic A's history.
  const isViewingHistory = !!report && !!activeTopic && report.topic !== activeTopic;

  return (
    <div className="flex flex-col gap-5 md:flex-row md:items-start">
      {/* Main column: run + report */}
      <div className="min-w-0 flex-1">
        <h1 className="text-xl font-bold">
          {isViewingHistory ? t("run.heading.history") : t("run.heading.run")}
        </h1>
        <p className="mb-4 mt-0.5 text-[13px] text-mut">
          {report
            ? t("run.subtitleTpl", {
                date: report.date,
                topic: report.topic,
                n: report.items.length,
                fresh: freshNote,
              })
            : t("run.subtitleEmpty")}
        </p>

        {isViewingHistory ? (
          // History view: hide the "Run" button (otherwise it would run the currently-active topic,
          // not the topic of the report on screen).
          <div className="flex items-center gap-2.5 rounded-[10px] border border-[#e7b4a0] bg-[#fbe7df] px-3.5 py-2.5 text-sm text-[#a23b1d]">
            <span>
              {t("run.viewingHistoryTpl", { active: activeTopic, viewing: report?.topic ?? "" })}
            </span>
            <button
              onClick={onBackToActiveLatest}
              className="ml-auto shrink-0 rounded-lg bg-terra px-3 py-1 text-xs font-semibold text-white"
            >
              {t("run.backToActiveTpl", { active: activeTopic })}
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2.5">
            <div className="flex-1 rounded-[10px] border border-line bg-panel px-3.5 py-2.5 text-sm text-mut">
              {t("run.currentTopicLabel")}
              <span className="font-semibold text-ink">{activeTopic || t("run.currentTopicEmpty")}</span>
            </div>
            <button
              onClick={() => activeTopic && onRun(activeTopic)}
              disabled={running || switching || !activeTopic}
              className="rounded-[10px] bg-terra px-5 font-semibold text-white disabled:opacity-50"
            >
              {running ? t("run.runBtnRunning") : t("run.runBtnLabel")}
            </button>
          </div>
        )}

        {/* Show the progress bar while a run is in flight; remount on each new run via `key`
            so elapsed-time counter resets to zero each time. */}
        {running && <RunProgress key={runMsg} />}

        {runMsg && <p className="mb-1 mt-2 text-xs text-mut">{runMsg}</p>}

        <p className="mb-2 mt-1.5 text-xs text-mut">{t("run.helper")}</p>

        {report && report.items.length > 0 ? (
          <ReportList items={report.items} starred={starred} onToggle={onToggle} />
        ) : (
          !running && (
            <div className="py-10 text-center text-sm text-mut">
              {report ? t("run.emptyResult.thisRun") : t("run.emptyResult.firstRun")}
            </div>
          )
        )}
      </div>

      {/* Right column: topic management — always shows the current active topic (independent of "history" in the main column) */}
      <TopicPanel
        topics={topics}
        activeTopic={activeTopic}
        sources={isViewingHistory ? [] : sources}
        scrapedSubreddits={isViewingHistory ? [] : scrapedSubreddits}
        onSwitch={onSwitchTopic}
        onDelete={onDeleteTopic}
        onToggleAutoDaily={onToggleAutoDaily}
        switching={switching}
      />
    </div>
  );
}
