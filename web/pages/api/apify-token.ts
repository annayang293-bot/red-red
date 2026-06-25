/** /api/apify-token — manage the caller's workspace Apify token (BYOK Phase 1-B).
 *
 *   GET    → { configured, last6, username, validated_at }  (never returns ciphertext/plaintext)
 *   POST   { token } → validate via Apify GET /users/me → AES-256-GCM encrypt → upsert
 *   DELETE → remove the workspace's token row
 *
 * Auth: the browser sends `Authorization: Bearer <supabase access_token>`. We verify it server-side
 * (auth.getUser) to get the user, then resolve the workspace they OWN (token belongs to the owner;
 * Phase 1 = one owned workspace per user — enforced by a UNIQUE(owner_id) constraint). All DB
 * access uses the service-role key, and the apify_credentials table is server-only (RLS + REVOKE),
 * so the secret never touches the browser. Error responses are codes only (UI localizes them).
 */
import type { NextApiRequest, NextApiResponse } from "next";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import { encryptToken } from "@/lib/crypto";
import { resolveCaller } from "@/lib/auth";

const APIFY_API = "https://api.apify.com/v2";
const APIFY_TIMEOUT_MS = 8000;
const TOKEN_MIN_LEN = 20;
const TOKEN_MAX_LEN = 256;

/** Confirm the token is live by calling Apify (Bearer header — never the token in the URL/query).
 *  Only a 401/403 means the token is genuinely bad; 429/5xx/timeout/network are transient and must
 *  NOT be reported as "invalid token" (we'd wrongly push the user to delete a valid token). */
async function validateApifyToken(
  token: string,
): Promise<{ ok: boolean; username?: string; transient?: boolean }> {
  try {
    const r = await fetch(`${APIFY_API}/users/me`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(APIFY_TIMEOUT_MS),
    });
    if (r.status === 401 || r.status === 403) return { ok: false }; // genuinely invalid
    if (!r.ok) return { ok: false, transient: true }; // 429 / 5xx → can't verify right now
    const j = (await r.json()) as { data?: { username?: string } };
    return { ok: true, username: j?.data?.username };
  } catch {
    return { ok: false, transient: true }; // network error or timeout
  }
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!["GET", "POST", "DELETE"].includes(req.method ?? "")) {
    res.setHeader("Allow", "GET, POST, DELETE");
    return res.status(405).json({ error: "method_not_allowed" });
  }

  try {
    const caller = await resolveCaller(req);
    if (!caller) return res.status(401).json({ error: "unauthorized" });
    const sb = getSupabaseAdmin();

    if (req.method === "GET") {
      const { data, error } = await sb
        .from("apify_credentials")
        .select("token_last6, account_username, validated_at")
        .eq("workspace_id", caller.workspaceId)
        .maybeSingle();
      if (error) {
        console.error("[apify-token GET] query error:", error.message);
        return res.status(500).json({ error: "server_error" });
      }
      return res.status(200).json({
        configured: Boolean(data),
        last6: data?.token_last6 ?? null,
        username: data?.account_username ?? null,
        validated_at: data?.validated_at ?? null,
      });
    }

    if (req.method === "POST") {
      const token = String(req.body?.token ?? "").trim();
      if (!token) return res.status(400).json({ error: "missing_token" });
      if (token.length < TOKEN_MIN_LEN || token.length > TOKEN_MAX_LEN) {
        return res.status(400).json({ error: "bad_token_format" });
      }

      const v = await validateApifyToken(token);
      if (!v.ok) {
        // Codes only — the UI maps them to localized text (lib/i18n.ts).
        if (v.transient) return res.status(502).json({ error: "apify_unreachable" });
        return res.status(400).json({ error: "invalid_token" });
      }

      const enc = encryptToken(token, caller.workspaceId); // AAD = workspace_id
      const { error } = await sb.from("apify_credentials").upsert(
        {
          workspace_id: caller.workspaceId,
          ciphertext: enc.ciphertext,
          nonce: enc.nonce,
          auth_tag: enc.authTag,
          key_version: 1,
          token_last6: token.slice(-6),
          account_username: v.username ?? null,
          validated_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
        { onConflict: "workspace_id" },
      );
      if (error) {
        console.error("[apify-token POST] store error:", error.message);
        return res.status(500).json({ error: "store_failed" });
      }
      return res.status(200).json({ ok: true, last6: token.slice(-6), username: v.username ?? null });
    }

    // DELETE
    const { error } = await sb
      .from("apify_credentials")
      .delete()
      .eq("workspace_id", caller.workspaceId);
    if (error) {
      console.error("[apify-token DELETE] delete error:", error.message);
      return res.status(500).json({ error: "delete_failed" });
    }
    return res.status(200).json({ ok: true });
  } catch (e) {
    console.error("[apify-token] unhandled:", e instanceof Error ? e.message : String(e));
    return res.status(500).json({ error: "server_error" });
  }
}
