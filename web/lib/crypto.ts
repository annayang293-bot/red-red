/**
 * Server-only AES-256-GCM helpers for the BYOK token vault (Phase 1-B).
 *
 * NEVER import this from client/browser code — it uses the Node `crypto` module and reads the
 * secret master key TOKEN_ENC_KEY (server env only). Only API routes import it.
 *
 * Format: the master key is 32 random bytes, base64-encoded, in env var TOKEN_ENC_KEY.
 * Each encryption uses a fresh random 12-byte nonce and binds the ciphertext to `aad`
 * (we pass the workspace_id) so a row can't be decrypted under a different workspace.
 * The Phase 2 Python decrypt side MUST use the identical scheme + the same AAD.
 */
import { createCipheriv, createDecipheriv, randomBytes } from "crypto";

function getKey(): Buffer {
  const b64 = process.env.TOKEN_ENC_KEY;
  if (!b64) throw new Error("Missing TOKEN_ENC_KEY (server env).");
  const key = Buffer.from(b64, "base64");
  if (key.length !== 32) {
    throw new Error(`TOKEN_ENC_KEY must decode to 32 bytes, got ${key.length}.`);
  }
  return key;
}

export interface EncryptedToken {
  ciphertext: string; // base64
  nonce: string; // base64 (12 bytes)
  authTag: string; // base64 (16 bytes)
}

/** Encrypt `plaintext`, binding it to `aad` (the workspace_id). */
export function encryptToken(plaintext: string, aad: string): EncryptedToken {
  const nonce = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", getKey(), nonce);
  cipher.setAAD(Buffer.from(aad, "utf8"));
  const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  return {
    ciphertext: ct.toString("base64"),
    nonce: nonce.toString("base64"),
    authTag: cipher.getAuthTag().toString("base64"),
  };
}

/** Decrypt; throws if the auth tag / AAD doesn't verify (tampered or wrong workspace). */
export function decryptToken(enc: EncryptedToken, aad: string): string {
  const decipher = createDecipheriv("aes-256-gcm", getKey(), Buffer.from(enc.nonce, "base64"));
  decipher.setAAD(Buffer.from(aad, "utf8"));
  decipher.setAuthTag(Buffer.from(enc.authTag, "base64"));
  const pt = Buffer.concat([
    decipher.update(Buffer.from(enc.ciphertext, "base64")),
    decipher.final(),
  ]);
  return pt.toString("utf8");
}
