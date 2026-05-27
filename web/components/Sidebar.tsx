import { TabKey } from "@/lib/types";

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: "run", label: "跑一次", icon: "🚀" },
  { key: "star", label: "精选库", icon: "⭐" },
  { key: "history", label: "历史", icon: "📅" },
  { key: "set", label: "设置", icon: "⚙️" },
];

export default function Sidebar({
  active,
  onChange,
  starCount,
}: {
  active: TabKey;
  onChange: (t: TabKey) => void;
  starCount: number;
}) {
  return (
    <aside className="w-52 shrink-0 border-r border-line bg-panel p-3 md:sticky md:top-0 md:h-screen">
      <div className="px-3 pb-4 pt-2">
        <div className="text-[15px] font-bold text-terra">🔥 热点选题</div>
        <div className="mt-0.5 text-[11px] text-mut">系统① · 给小红书选选题</div>
      </div>
      <nav className="flex flex-col gap-1">
        {TABS.map((t) => {
          const on = t.key === active;
          return (
            <button
              key={t.key}
              onClick={() => onChange(t.key)}
              className={
                "flex items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors " +
                (on
                  ? "bg-terra font-semibold text-white"
                  : "text-ink hover:bg-terrasoft")
              }
            >
              <span>{t.icon}</span>
              <span>{t.label}</span>
              {t.key === "star" && starCount > 0 && (
                <span className={"ml-auto text-xs " + (on ? "text-white/90" : "text-mut")}>
                  {starCount}
                </span>
              )}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
