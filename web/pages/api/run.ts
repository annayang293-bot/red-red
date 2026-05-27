/** POST /api/run { topic } — 触发一次主线跑(Node 起 Python 子进程)→ 落库 → 返回 run 概要。
 *
 *  架构:Node 不重写 pipeline,直接以子进程跑 `python -m pipeline.run_once <topic>`
 *  (单次 30–60s,Vercel Fluid 300s 够)。子进程 stdout 末行是结果 JSON。 */
import type { NextApiRequest, NextApiResponse } from "next";
import { spawn } from "child_process";
import path from "path";
import { ensureMethod, failError } from "@/lib/api";

// next dev/build 的 cwd = web/;pipeline 包在上一级 system1-app/。
const PIPELINE_CWD = path.resolve(process.cwd(), "..");
const PYTHON_BIN = process.env.PYTHON_BIN || "python3";
const RUN_TIMEOUT_MS = 180_000;

type RunResult = {
  ok: boolean;
  run_id?: number;
  topic?: string;
  status?: string;
  ai_mode?: string;
  posts?: number;
  top?: number;
  failed_sources?: string[];
  sanity_status?: string;
  error?: string;
};

function runPipeline(topic: string): Promise<RunResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, ["-m", "pipeline.run_once", topic], {
      cwd: PIPELINE_CWD,
      env: process.env,
    });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d.toString()));
    child.stderr.on("data", (d) => (err += d.toString()));

    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("pipeline 超时(>180s)"));
    }, RUN_TIMEOUT_MS);

    child.on("error", (e) => {
      clearTimeout(timer);
      reject(e);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      const lastLine = out.trim().split("\n").filter(Boolean).pop() || "";
      try {
        resolve(JSON.parse(lastLine) as RunResult);
      } catch {
        reject(
          new Error(
            `pipeline 输出无法解析(exit ${code}): ${lastLine || err.slice(-500) || "(空)"}`
          )
        );
      }
    });
  });
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ensureMethod(req, res, ["POST"])) return;
  const topic = String(req.body?.topic ?? "").trim();
  if (!topic) return res.status(400).json({ error: "missing_topic" });
  try {
    const result = await runPipeline(topic);
    if (!result.ok) {
      return res.status(502).json({ error: "pipeline_failed", ...result });
    }
    res.status(200).json(result);
  } catch (e) {
    failError(res, e);
  }
}
