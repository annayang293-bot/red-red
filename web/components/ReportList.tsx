import { ReportItem, tierColor } from "@/lib/types";
import { useT } from "@/lib/i18n";

export function Row({
  item,
  starred,
  onToggle,
  showTierTag = false,
}: {
  item: ReportItem;
  starred: boolean;
  onToggle: (id: string) => void;
  showTierTag?: boolean;
}) {
  const { t } = useT();
  const metrics = item.likes
    ? `👍 ${item.likes} · 💬 ${item.comments}`
    : t("list.product_hunt");
  return (
    <div className="flex gap-3 border-b border-line py-3 last:border-b-0">
      <button
        onClick={() => onToggle(item.id)}
        title={starred ? t("list.unstarTitle") : t("list.starTitle")}
        className={
          "shrink-0 text-lg leading-7 transition-colors " +
          (starred ? "text-mid" : "text-line hover:text-mid")
        }
      >
        {starred ? "★" : "☆"}
      </button>
      <div className="min-w-0 flex-1">
        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t("list.openOriginalAria")}
          className="block text-[15px] font-semibold leading-snug text-ink hover:text-terra"
        >
          {showTierTag && <span className="mr-1">{item.tier_emoji}</span>}
          {item.rank}. {item.title}
        </a>
        <div className="mb-1 mt-0.5 text-xs text-mut">
          {item.source} · {metrics}
          {item.is_new === false && (
            <span className="ml-1.5 rounded bg-[#efe2d2] px-1.5 py-0.5 text-[10px] text-[#9a6a3a]">
              {t("list.recurring")}
            </span>
          )}
        </div>
        <div className="text-[13px] text-ink/80">{item.comment}</div>
      </div>
    </div>
  );
}

export default function ReportList({
  items,
  starred,
  onToggle,
}: {
  items: ReportItem[];
  starred: Set<string>;
  onToggle: (id: string) => void;
}) {
  // Group by tier (keep hot_score order inside each group)
  const tiers: { emoji: string; name: string; desc: string; items: ReportItem[] }[] = [];
  for (const it of items) {
    let g = tiers.find((t) => t.emoji === it.tier_emoji && t.name === it.tier_name);
    if (!g) {
      g = { emoji: it.tier_emoji, name: it.tier_name, desc: it.tier_desc, items: [] };
      tiers.push(g);
    }
    g.items.push(it);
  }
  // Sort tiers by **priority**: strong → medium → weak (don't sort by "first appearance", otherwise
  // under real AI tiering, strong items would sometimes appear below medium).
  const TIER_ORDER: Record<string, number> = { 强迁移: 0, 中等迁移: 1, 弱迁移: 2 };
  tiers.sort((a, b) => (TIER_ORDER[a.name] ?? 9) - (TIER_ORDER[b.name] ?? 9));

  return (
    <div>
      {tiers.map((t) => (
        <section key={t.emoji + t.name}>
          <div className="mb-1 mt-6 flex items-baseline gap-2 border-b-2 border-line pb-1.5 text-base">
            <span>{t.emoji}</span>
            <b className={tierColor[t.emoji] ?? "text-weak"}>{t.name}</b>
            <span className="text-xs font-normal text-mut">{t.desc}</span>
          </div>
          <div>
            {t.items.map((it) => (
              <Row
                key={it.id}
                item={it}
                starred={starred.has(it.id)}
                onToggle={onToggle}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
