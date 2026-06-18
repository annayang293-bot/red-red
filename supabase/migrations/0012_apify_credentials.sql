-- ============================================================
-- 0012_apify_credentials.sql
-- Phase 1 of the multi-user / BYOK plan (docs/SYSTEM1_BYOK_PLAN.md).
--
-- The encrypted per-workspace Apify-token vault. One row per workspace holds that workspace's
-- Apify token, stored ONLY as AES-256-GCM ciphertext — plaintext never touches the database.
-- The encryption master key (TOKEN_ENC_KEY) lives in server env vars (Vercel + GitHub secret),
-- never in the DB, so a DB leak alone does not reveal any token.
--
-- ACCESS MODEL — this table is SERVER-ONLY:
--   All reads/writes go through server API routes that use the SECRET (service-role) key, because
--   the encryption key is server-side and the row's ciphertext should never reach the browser.
--   So we ENABLE RLS but add NO policies and NO grant to `authenticated`/`anon` — that makes the
--   table invisible to every per-user (anon-key) query, while the service role (which bypasses
--   RLS and already has privileges) keeps full access. Maximum lockdown for a secrets table.
--
-- IMPORTANT for the Phase 1-B encryption helper: the AES-256-GCM call MUST pass workspace_id (as
-- UTF-8 bytes) as the AAD (Additional Authenticated Data — bytes that are authenticated but not
-- encrypted). This binds each ciphertext to its workspace: decrypting under a different
-- workspace_id fails the auth-tag check, so a token row can't be moved across workspaces
-- undetected. (No column needed — AAD lives only in the crypto call.)
-- ============================================================

CREATE TABLE apify_credentials (
  workspace_id     UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  -- AES-256-GCM outputs (all base64-encoded text):
  ciphertext       TEXT NOT NULL,                 -- the encrypted token
  nonce            TEXT NOT NULL,                 -- 12-byte GCM nonce/IV, unique per encryption
  auth_tag         TEXT NOT NULL,                 -- 16-byte GCM authentication tag (tamper check)
  key_version      SMALLINT NOT NULL DEFAULT 1,   -- which master-key generation encrypted this row
  -- Display / audit (safe, non-secret):
  token_last6      TEXT,                          -- last 6 chars, shown in UI so a user IDs the token
  account_username TEXT,                          -- Apify username from GET /users/me at validation
  validated_at     TIMESTAMPTZ,                   -- set by the API route AFTER GET /users/me confirms live; NULL = unverified
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Keep updated_at fresh automatically (function defined in 0001; this table post-dates the
-- bulk attach there, so wire it explicitly).
CREATE TRIGGER set_updated_at
  BEFORE UPDATE ON apify_credentials
  FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- Server-only lockdown. RLS on + zero policies already makes RLS default-deny every row, but
-- Supabase's default privileges auto-GRANT ALL on new public tables to anon/authenticated, so we
-- must EXPLICITLY REVOKE to lock the table at the privilege layer too (PostgREST denies before RLS
-- even runs). The service_role keeps its own grants + bypasses RLS, so server API routes are
-- unaffected.
ALTER TABLE apify_credentials ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON apify_credentials FROM anon, authenticated;
