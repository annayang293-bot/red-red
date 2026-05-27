/** API 路由小工具:方法守卫 + 统一错误 JSON。 */
import type { NextApiRequest, NextApiResponse } from "next";

export function ensureMethod(
  req: NextApiRequest,
  res: NextApiResponse,
  allowed: string[]
): boolean {
  if (!allowed.includes(req.method || "")) {
    res.setHeader("Allow", allowed.join(", "));
    res.status(405).json({ error: "method_not_allowed" });
    return false;
  }
  return true;
}

export function failError(res: NextApiResponse, e: unknown): void {
  const message = e instanceof Error ? e.message : String(e);
  res.status(500).json({ error: "server_error", message });
}
