import { useT } from "@/lib/i18n";

export default function SettingsTab() {
  const { t } = useT();
  return (
    <div>
      <h1 className="text-xl font-bold">{t("set.heading")}</h1>
      <p className="mb-4 mt-0.5 text-[13px] text-mut">{t("set.subtitle")}</p>

      <div className="rounded-xl border border-line bg-panel p-4">
        <div className="text-[13px] text-ink/80">{t("set.hotnessNote")}</div>
      </div>

      <div className="py-9 text-center text-sm text-mut">{t("set.placeholder")}</div>
    </div>
  );
}
