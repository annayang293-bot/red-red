/** Browser fetch that attaches the Supabase access token as a Bearer header (System ① BYOK).
 *
 * Phase 3 made the per-workspace read/write API routes auth-gated (they resolve the caller via
 * resolveCaller → workspace). Any UI fetch to those routes must go through this helper so the
 * server can identify the workspace. The whole app is already behind <AuthGate>, so a session
 * normally exists; if it somehow doesn't, the request goes out without a token and the route 401s
 * (fail closed) rather than silently reading another workspace's data.
 *
 * Also sets Content-Type: application/json whenever a body is present (every JSON POST/DELETE here).
 */
import { getSupabaseBrowser } from "@/lib/supabase-browser";

/** localStorage key for the workspace switcher's current selection (Phase 3 sharing). */
export const CURRENT_WS_KEY = "current_workspace_id";

async function sendWithToken(input: string, init?: RequestInit): Promise<Response> {
  const {
    data: { session },
  } = await getSupabaseBrowser().auth.getSession();
  const headers = new Headers(init?.headers);
  if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
  // Active workspace (from the switcher). Absent → the server defaults to the user's own workspace.
  if (typeof window !== "undefined") {
    const wsId = window.localStorage.getItem(CURRENT_WS_KEY);
    if (wsId) headers.set("x-workspace-id", wsId);
  }
  if (init?.body) headers.set("Content-Type", "application/json");
  return fetch(input, { ...init, headers });
}

export async function authedFetch(input: string, init?: RequestInit): Promise<Response> {
  let res = await sendWithToken(input, init);
  // Refresh-and-retry once on 401. On page load, when the stored access token is expired, the
  // background refresh can race concurrent calls — getSession() may hand back the stale (expired)
  // token, which the server rejects. Forcing a refresh then retrying eliminates that race (and also
  // recovers from a token that simply expired). A 401 means the server rejected the request before
  // doing any work, so retrying is safe for POST/DELETE too (no double-execution).
  if (res.status === 401) {
    await getSupabaseBrowser().auth.refreshSession();
    res = await sendWithToken(input, init);
  }
  return res;
}
