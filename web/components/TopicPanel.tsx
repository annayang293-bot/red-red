import { useState } from "react";
import { useT } from "@/lib/i18n";

export type TopicLite = { topic_id: number; keyword: string; status: string };

export default function TopicPanel({
  topics,
  activeTopic,
  sources,
  scrapedSubreddits,
  onSwitch,
  onDelete,
  switching,
}: {
  topics: TopicLite[];
  activeTopic: string;
  sources: string[];
  scrapedSubreddits: string[];   // Full fetched list (includes those without Top items)
  onSwitch: (keyword: string) => void;
  onDelete: (topicId: number, keyword: string) => void;
  switching: boolean;
}) {
  const { t } = useT();
  const [newTopic, setNewTopic] = useState("");
  const others = topics.filter((t) => t.keyword !== activeTopic);

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
          <button
            onClick={() => {
              const k = newTopic.trim();
              if (k) {
                onSwitch(k);
                setNewTopic("");
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
