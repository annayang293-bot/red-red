import { useMemo, useState } from "react";
import { ReportItem } from "@/lib/types";
import { Row } from "./ReportList";
import { useT } from "@/lib/i18n";

export default function StarredTab({
  items,
  starred,
  onToggle,
}: {
  items: ReportItem[];
  starred: Set<string>;
  onToggle: (id: string) => void;
}) {
  const { t } = useT();
  // Filter by source (empty = all). After accumulating across runs, the common axis to browse back
  // through is "by subreddit / platform".
  const [sourceFilter, setSourceFilter] = useState<string>("");

  // Per-source counts (display in descending count, stable order).
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
      <h1 className="text-xl font-bold">{t("star.heading")}</h1>
      <p className="mb-4 mt-0.5 text-[13px] text-mut">{t("star.subtitle")}</p>

      {/* Show the filter row only when there are ≥2 sources (1 source = nothing to filter against) */}
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
            {t("star.all")}({items.length})
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
        <div className="py-10 text-center text-sm text-mut">{t("star.empty")}</div>
      ) : filtered.length === 0 ? (
        <div className="py-10 text-center text-sm text-mut">{t("star.emptyForFilter")}</div>
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
