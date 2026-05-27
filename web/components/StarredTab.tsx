import { useMemo, useState } from "react";
import { ReportItem } from "@/lib/types";
import { Row } from "./ReportList";

export default function StarredTab({
  items,
  starred,
  onToggle,
}: {
  items: ReportItem[];
  starred: Set<string>;
  onToggle: (id: string) => void;
}) {
  // 按"来源"筛选(空 = 全部)。跨多次跑累积后,常用维度是按版块/平台挑回看。
  const [sourceFilter, setSourceFilter] = useState<string>("");

  // 各来源计数(按数量降序展示,稳定)
  const sourceCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const it of items) {
      const s = it.source || "";
      if (!s) continue;
      m.set(s, (m.get(s) || 0) + 1);
    }
    return Array.from(m.entries()).sort((a, b) => b[1] - a[1]);
  }, [items]);

  const filtered = sourceFilter
    ? items.filter((it) => it.source === sourceFilter)
    : items;

  return (
    <div>
      <h1 className="text-xl font-bold">⭐ 精选库</h1>
      <p className="mb-4 mt-0.5 text-[13px] text-mut">
        你收藏过的选题(跨多次跑累积,已保存)
      </p>

      {/* 只有 ≥2 个来源时才显示筛选(1 个来源没必要筛) */}
      {sourceCounts.length > 1 && (
        <div className="mb-4 flex flex-wrap gap-1.5">
          <button
            onClick={() => setSourceFilter("")}
            className={
              "rounded-full px-3 py-1 text-xs transition-colors " +
              (sourceFilter === ""
                ? "bg-terra font-semibold text-white"
                : "bg-terrasoft text-terra hover:bg-terra/20")
            }
          >
            全部({items.length})
          </button>
          {sourceCounts.map(([src, n]) => (
            <button
              key={src}
              onClick={() => setSourceFilter(src)}
              className={
                "rounded-full px-3 py-1 text-xs transition-colors " +
                (sourceFilter === src
                  ? "bg-terra font-semibold text-white"
                  : "bg-terrasoft text-terra hover:bg-terra/20")
              }
            >
              {src}({n})
            </button>
          ))}
        </div>
      )}

      {items.length === 0 ? (
        <div className="py-10 text-center text-sm text-mut">
          还没有收藏 —— 去&ldquo;跑一次&rdquo;点标题左侧的 ☆
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-10 text-center text-sm text-mut">
          这个来源还没收藏(换一个筛选或选&ldquo;全部&rdquo;)
        </div>
      ) : (
        <div>
          {filtered.map((it) => (
            <Row
              key={it.id}
              item={it}
              starred={starred.has(it.id)}
              onToggle={onToggle}
              showTierTag
            />
          ))}
        </div>
      )}
    </div>
  );
}
