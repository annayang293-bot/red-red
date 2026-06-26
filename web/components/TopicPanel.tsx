import { useState } from "react";
import { useT } from "@/lib/i18n";

export type TopicLite = {
  topic_id: number;
  keyword: string;
  status: string;
  auto_daily?: boolean;
};

export default function TopicPanel({
  topics,
  activeTopic,
  sources,
  scrapedSubreddits,
  onSwitch,
  onDelete,
  onToggleAutoDaily,
  switching,
}: {
  topics: TopicLite[];
  activeTopic: string;
  sources: string[];
  scrapedSubreddits: string[];   // Full fetched list (includes those without Top items)
  // onSwitch: optional second arg is the user-supplied mapping hint (option 3, Anna 2026-05-28);
  // wired to topics.mapping_hint on the backend. Existing callers passing only keyword still work.
  onSwitch: (keyword: string, hint?: string) => void;
  onDelete: (topicId: number, keyword: string) => void;
  // Toggle a topic's daily auto-run opt-in (Phase 3-7 auto_daily feature).
  onToggleAutoDaily: (topicId: number, next: boolean) => void;
  switching: boolean;
}) {
  const { t } = useT();
  const activeTopicObj = topics.find((tt) => tt.keyword === activeTopic);
  const [newTopic, setNewTopic] = useState("");
  const [newHint, setNewHint] = useState("");
  const [showHint, setShowHint] = useState(false);
  const others = topics.filter((tp) => tp.keyword !== activeTopic);

  return (
    <aside className="w-full shrink-0 md:w-56">
      <div className="rounded-xl border border-line bg-panel p-4">
        <h2 className="text-sm font-bold">{t("topic.heading")}</h2>

        <div className="mt-2 flex items-center gap-2">
          <b className="text-[15px] text-ink">{activeTopic || t("run.currentTopicEmpty")}</b>
          <span className="rounded-full bg-terrasoft px-2 py-0.5 text-[11px] text-terra">
            {t("topic.activeBadge")}
          </span>
        </div>

        {activeTopicObj && (
          <div className="mt-2">
            <label className="flex cursor-pointer items-center gap-2 text-[12px] text-ink/80">
              <input
                type="checkbox"
                checked={!!activeTopicObj.auto_daily}
                onChange={(e) => onToggleAutoDaily(activeTopicObj.topic_id, e.target.checked)}
                className="accent-terra"
              />
              {t("topic.autoDaily")}
            </label>
            <div className="mt-0.5 text-[10px] text-mut">{t("topic.autoDailyHelp")}</div>
          </div>
        )}

        {scrapedSubreddits.length > 0 && (
          <div className="mt-2.5">
            <div className="mb-1 text-[11px] text-mut">{t("topic.scrapedAll")}</div>
            <div className="text-[12px] leading-relaxed text-ink/80">
              {scrapedSubreddits.map((s) => `r/${s}`).join(" · ")}
            </div>
          </div>
        )}

        {sources.length > 0 && (
          <div className="mt-2.5">
            <div className="mb-1 text-[11px] text-mut">{t("topic.inReport")}</div>
            <div className="text-[12px] leading-relaxed text-ink/80">
              {sources.join(" · ")}
            </div>
          </div>
        )}

        {others.length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-[11px] text-mut">{t("topic.others")}</div>
            <div className="flex flex-col gap-1">
              {others.map((tt) => (
                <div
                  key={tt.topic_id}
                  className="group flex items-center gap-1 rounded-lg hover:bg-terrasoft"
                >
                  <button
                    onClick={() => onSwitch(tt.keyword)}
                    disabled={switching}
                    className="flex-1 truncate px-2.5 py-1.5 text-left text-[13px] text-ink disabled:opacity-50"
                  >
                    {tt.keyword}
                  </button>
                  <input
                    type="checkbox"
                    checked={!!tt.auto_daily}
                    onChange={(e) => onToggleAutoDaily(tt.topic_id, e.target.checked)}
                    title={t("topic.autoDaily")}
                    aria-label={t("topic.autoDaily")}
                    className="shrink-0 accent-terra"
                  />
                  <button
                    onClick={() => onDelete(tt.topic_id, tt.keyword)}
                    disabled={switching}
                    title={t("topic.deleteTitleTpl", { kw: tt.keyword })}
                    className="px-1.5 text-mut opacity-0 transition-opacity hover:text-[#c2562a] group-hover:opacity-100 disabled:opacity-30"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-3 border-t border-line pt-3">
          <div className="mb-1 text-[11px] text-mut">{t("topic.newLabel")}</div>
          <input
            value={newTopic}
            onChange={(e) => setNewTopic(e.target.value)}
            placeholder={t("topic.newPlaceholder")}
            className="w-full rounded-lg border border-line bg-white px-2.5 py-1.5 text-[13px] outline-none focus:border-terra"
          />

          {/* Optional mapping-hint disclosure (option 3, Anna 2026-05-28). Hidden by default to
              keep the panel light; expand-on-click for users who want to steer the LLM. */}
          {!showHint ? (
            <button
              onClick={() => setShowHint(true)}
              className="mt-1.5 text-[11px] text-mut underline hover:text-terra"
            >
              {t("topic.addHintLink")}
            </button>
          ) : (
            <div className="mt-1.5">
              <div className="mb-1 text-[11px] text-mut">{t("topic.hintLabel")}</div>
              <textarea
                value={newHint}
                onChange={(e) => setNewHint(e.target.value)}
                placeholder={t("topic.hintPlaceholder")}
                rows={2}
                className="w-full resize-none rounded-lg border border-line bg-white px-2.5 py-1.5 text-[12px] outline-none focus:border-terra"
              />
              <div className="mt-0.5 text-[10px] text-mut">{t("topic.hintHelp")}</div>
            </div>
          )}

          <button
            onClick={() => {
              const k = newTopic.trim();
              if (k) {
                onSwitch(k, newHint.trim() || undefined);
                setNewTopic("");
                setNewHint("");
                setShowHint(false);
              }
            }}
            disabled={switching || !newTopic.trim()}
            className="mt-1.5 w-full rounded-lg bg-terra px-2.5 py-1.5 text-[13px] font-semibold text-white disabled:opacity-50"
          >
            {switching ? t("topic.switching") : t("topic.newBtn")}
          </button>
        </div>

        <p className="mt-3 text-[11px] leading-relaxed text-mut">{t("topic.footer")}</p>
      </div>
    </aside>
  );
}
