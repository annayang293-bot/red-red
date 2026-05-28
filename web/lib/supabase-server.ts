/**
 * Server-side Supabase client (only imported by API routes, never bundled to the browser).
 *
 * Uses the secret key (equivalent to service-role) over PostgREST. Key lives in server-side
 * process.env only; locally in web/.env.local (gitignored), in Vercel deploys via env vars (Step 8).
 *
 * Lazy build: missing key only throws at the moment of use, not at import / build time
 * (so a CI build without keys still passes).
 */
import { createClient, SupabaseClient } from "@supabase/supabase-js";

let _client: SupabaseClient | null = null;

export function getSupabaseAdmin(): SupabaseClient {
  if (_client) return _client;
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!url || !key) {
    throw new Error(
      "Missing SUPABASE_URL / SUPABASE_SECRET_KEY (locally in web/.env.local; configure as env vars on Vercel)"
    );
  }
  _client = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return _client;
}
