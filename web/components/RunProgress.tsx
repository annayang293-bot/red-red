/**
 * Time-based progress bar shown while /api/run is in flight.
 *
 * The Python pipeline runs as a subprocess and the API responds only when it finishes; we don't
 * have streaming progress yet. So this bar is **time-estimated** rather than reading real stage
 * signals. The phase boundaries (in seconds) are tuned to typical run durations observed in
 * production: fetch is the biggest slice (~25s), scoring is fast (~3s), AI review ~22s, DB save ~5s.
 *
 * Bar caps at 95% until the parent flips `running` to false (we never claim 100% before we know
 * the result), so the user doesn't see "100%" then have to wait another beat for the result text.
 *
 * Honest about being estimated: the phase label says "约" (approximately) and the seconds counter
 * shows real elapsed time. If pipeline takes longer than expected, the bar plateaus at 95% and the
 * phase label stays on the last phase — the user keeps seeing elapsed seconds tick up so they know
 * something's still happening.
 */
import { useEffect, useState } from "react";
import { useT } from "@/lib/i18n";

type Phase = { tkey: string; endSec: number };

// Phase boundaries (in seconds) — when elapsed crosses endSec, advance to the next phase.
const PHASES: Phase[] = [
  { tkey: "run.progress.fetch", endSec: 25 },   // 0–25s — Reddit/PH fetches
  { tkey: "run.progress.score", endSec: 28 },   // 25–28s — scoring + 3 gates + dedup + tags
  { tkey: "run.progress.review", endSec: 50 },  // 28–50s — LLM AI review
  { tkey: "run.progress.save", endSec: 58 },    // 50–58s — Supabase write
];
const TOTAL_SEC = 58;
const MAX_PCT = 95;

export default function RunProgress() {
  const { t } = useT();
  const [startTs] = useState(() => Date.now());
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setElapsedMs(Date.now() - startTs), 200);
    return () => clearInterval(id);
  }, [startTs]);

  const sec = elapsedMs / 1000;
  const phase = PHASES.find((p) => sec < p.endSec) ?? PHASES[PHASES.length - 1];
  const pct = Math.min(MAX_PCT, (sec / TOTAL_SEC) * 100);

  return (
    <div className="my-3">
      {/* The bar itself: warm cream track + terra fill. Width transitions smooth, no abrupt jumps. */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-terrasoft">
        <div
          className="h-full rounded-full bg-terra transition-[width] duration-200 ease-linear"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-1 flex items-center justify-between text-[11px] text-mut">
        <span>{t(phase.tkey)}</span>
        <span>
          {sec.toFixed(0)}s · {Math.round(pct)}%
        </span>
      </div>
    </div>
  );
}
