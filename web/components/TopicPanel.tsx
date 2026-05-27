import { useState } from "react";

export type TopicLite = { topic_id: number; keyword: string; status: string };

export default function TopicPanel({
  topics,
  activeTopic,
  sources,
  onSwitch,
  onDelete,
  switching,
}: {
  topics: TopicLite[];
  activeTopic: string;
  sources: string[];
  onSwitch: (keyword: string) => void;
  onDelete: (topicId: number, keyword: string) => void;
  switching: boolean;
}) {
  const [newTopic, setNewTopic] = useState("");
  const others = topics.filter((t) => t.keyword !== activeTopic);

  return (
    <aside className="w-full shrink-0 md:w-56">
      <div className="rounded-xl border border-line bg-panel p-4">
        <h2 className="text-sm font-bold">🎯 主题</h2>

        <div className="mt-2 flex items-center gap-2">
          <b className="text-[15px] text-ink">{activeTopic || "(还没设置)"}</b>
          <span className="rounded-full bg-terrasoft px-2 py-0.5 text-[11px] text-terra">
            当前
          </span>
        </div>

        {sources.length > 0 && (
          <div className="mt-2.5">
            <div className="mb-1 text-[11px] text-mut">本次内容来自:</div>
            <div className="text-[12px] leading-relaxed text-ink/80">
              {sources.join(" · ")}
            </div>
          </div>
        )}

        {others.length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-[11px] text-mut">切到别的主题:</div>
            <div className="flex flex-col gap-1">
              {others.map((t) => (
                <div
                  key={t.topic_id}
                  className="group flex items-center gap-1 rounded-lg hover:bg-terrasoft"
                >
                  <button
                    onClick={() => onSwitch(t.keyword)}
                    disabled={switching}
                    className="flex-1 truncate px-2.5 py-1.5 text-left text-[13px] text-ink disabled:opacity-50"
                  >
                    {t.keyword}
                  </button>
                  <button
                    onClick={() => onDelete(t.topic_id, t.keyword)}
                    disabled={switching}
                    title={`删除主题"${t.keyword}"(连同它的所有历史报告)`}
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
          <div className="mb-1 text-[11px] text-mut">新主题:</div>
          <input
            value={newTopic}
            onChange={(e) => setNewTopic(e.target.value)}
            placeholder="如:具身智能 / AI 编程"
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
            {switching ? "切换中…" : "建 + 切到它"}
          </button>
        </div>

        <p className="mt-3 text-[11px] leading-relaxed text-mut">
          一次只跑一个主题。换主题 = 旧的归档、新的启用,各自的报告/收藏历史分开存,不会混。
        </p>
      </div>
    </aside>
  );
}
