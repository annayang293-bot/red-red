/**
 * Browser-side Supabase client (System ① BYOK, Phase 0-B).
 *
 * Uses the ANON (publishable) key — safe to ship to the browser; Row Level Security is what
 * actually protects data. Distinct from supabase-server.ts, which uses the SECRET (service-role)
 * key server-side and BYPASSES RLS.
 *
 * NEXT_PUBLIC_* vars are inlined into the client bundle at `next build` time, so they MUST be
 * referenced literally (no dynamic key lookup) and MUST be set on Vercel before the prod build.
 * Locally they live in web/.env.local (gitignored).
 *
 * Auth config: persist the session (localStorage) + auto-refresh + detectSessionInUrl so the
 * magic-link callback (tokens arriving in the URL) is consumed automatically on page load.
 */
import { createClient, SupabaseClient } from "@supabase/supabase-js";

let _client: SupabaseClient | null = null;

/** True only when both public env vars are present (lets the UI show a clear "not configured"
 *  state instead of crashing before the anon key is added). */
export function hasSupabaseBrowserConfig(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  );
}

export function getSupabaseBrowser(): SupabaseClient {
  if (_client) return _client;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anon) {
    throw new Error(
      "Missing NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY (web/.env.local locally; Vercel env for prod).",
    );
  }
  _client = createClient(url, anon, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  });
  return _client;
}
