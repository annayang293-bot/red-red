import { TabKey } from "@/lib/types";
import { useT } from "@/lib/i18n";

const TABS: { key: TabKey; tkey: string; icon: string }[] = [
  { key: "run", tkey: "side.tab.run", icon: "🚀" },
  { key: "star", tkey: "side.tab.star", icon: "⭐" },
  { key: "history", tkey: "side.tab.history", icon: "📅" },
  { key: "set", tkey: "side.tab.set", icon: "⚙️" },
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
  const { t, lang, setLang } = useT();
  return (
    <aside className="w-52 shrink-0 border-r border-line bg-panel p-3 md:sticky md:top-0 md:h-screen md:flex md:flex-col">
      <div className="px-3 pb-4 pt-2">
        <div className="text-[15px] font-bold text-terra">{t("side.brand")}</div>
        <div className="mt-0.5 text-[11px] text-mut">{t("side.tag")}</div>
      </div>
      <nav className="flex flex-col gap-1">
        {TABS.map((tab) => {
          const on = tab.key === active;
          return (
            <button
              key={tab.key}
              onClick={() => onChange(tab.key)}
              className={
                "flex items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors " +
                (on
                  ? "bg-terra font-semibold text-white"
                  : "text-ink hover:bg-terrasoft")
              }
            >
              <span>{tab.icon}</span>
              <span>{t(tab.tkey)}</span>
              {tab.key === "star" && starCount > 0 && (
                <span className={"ml-auto text-xs " + (on ? "text-white/90" : "text-mut")}>
                  {starCount}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Language toggle — bottom of the sidebar so it's out of the way but always reachable */}
      <div className="mt-auto flex items-center gap-1 px-3 pt-4 text-[11px] text-mut">
        <span>{t("side.langLabel")}</span>
        <button
          onClick={() => setLang("zh")}
          className={
            "rounded px-1.5 py-0.5 " +
            (lang === "zh" ? "bg-terrasoft font-semibold text-terra" : "hover:bg-terrasoft")
          }
        >
          {t("side.langZh")}
        </button>
        <span className="text-line">|</span>
        <button
          onClick={() => setLang("en")}
          className={
            "rounded px-1.5 py-0.5 " +
            (lang === "en" ? "bg-terrasoft font-semibold text-terra" : "hover:bg-terrasoft")
          }
        >
          {t("side.langEn")}
        </button>
      </div>
    </aside>
  );
}
