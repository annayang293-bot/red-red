"""BYOK (Phase 2): resolve + decrypt a workspace's Apify token for the runner.

Mirrors web/lib/crypto.ts (AES-256-GCM, AAD = workspace_id). The 32-byte master key is base64 in
env `TOKEN_ENC_KEY` (a GitHub Actions secret on the runner). The encrypted token lives in Supabase
`apify_credentials` (read with the service key, which bypasses RLS). Plaintext only ever lives in
process memory and is NEVER logged.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _master_key() -> bytes:
    b64 = os.environ.get("TOKEN_ENC_KEY")
    if not b64:
        raise RuntimeError("TOKEN_ENC_KEY missing (set it in env / GitHub Actions secret).")
    key = base64.b64decode(b64)
    if len(key) != 32:
        raise RuntimeError(f"TOKEN_ENC_KEY must decode to 32 bytes, got {len(key)}.")
    return key


def decrypt_token(ciphertext_b64: str, nonce_b64: str, auth_tag_b64: str, aad: str) -> str:
    """Decrypt one apify_credentials row. `aad` must be the workspace_id (UTF-8), matching how the
    Node side encrypted it — a wrong workspace_id fails the GCM auth-tag check and raises."""
    nonce = base64.b64decode(nonce_b64)
    # cryptography's AESGCM expects the ciphertext with the 16-byte tag appended; the Node side
    # stores them in separate columns, so re-join here.
    ct_and_tag = base64.b64decode(ciphertext_b64) + base64.b64decode(auth_tag_b64)
    pt = AESGCM(_master_key()).decrypt(nonce, ct_and_tag, aad.encode("utf-8"))
    return pt.decode("utf-8")


def resolve_apify_token(workspace_id: str | None) -> str:
    """The Apify token this run should use.

    - workspace_id given → fetch that workspace's encrypted token from Supabase + decrypt (BYOK).
    - workspace_id None  → fall back to the project token in env APIFY_TOKEN (legacy / daily-cron
      path, unchanged).
    Never logs the token.
    """
    if not workspace_id:
        tok = os.environ.get("APIFY_TOKEN")
        if not tok:
            raise RuntimeError("APIFY_TOKEN missing and no --workspace-id given.")
        return tok

    from .supa import get_client  # service-key client; bypasses RLS

    res = (
        get_client()
        .table("apify_credentials")
        .select("ciphertext, nonce, auth_tag")
        .eq("workspace_id", workspace_id)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise RuntimeError(f"workspace {workspace_id} has no Apify token configured.")
    row = rows[0]
    return decrypt_token(row["ciphertext"], row["nonce"], row["auth_tag"], workspace_id)
