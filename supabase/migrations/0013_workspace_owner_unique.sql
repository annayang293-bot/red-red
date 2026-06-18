-- ============================================================
-- 0013_workspace_owner_unique.sql
-- BYOK Phase 1 review follow-ups (docs/SYSTEM1_BYOK_PLAN.md).
--
-- (1) One owned workspace per user. The Phase 1 model is "each user owns exactly one workspace"
--     (auto-created at signup by handle_new_user). /api/apify-token resolves "the caller's
--     workspace" as the one they own; without this constraint a user owning multiple workspaces
--     would make that resolution ambiguous. Enforce it at the DB. (Relax later if a user should
--     ever own multiple workspaces — then /api/apify-token must take an explicit workspace_id.)
--
-- (2) Be explicit about service_role access to the secrets table instead of relying on Supabase's
--     default-privilege behavior. anon/authenticated stay REVOKED (set in 0012).
-- ============================================================

ALTER TABLE workspaces ADD CONSTRAINT workspaces_owner_id_unique UNIQUE (owner_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON apify_credentials TO service_role;
