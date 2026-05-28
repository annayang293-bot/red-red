"""Supabase client factory — reads URL + secret key from env / `.env`.

Data read/write goes through PostgREST (HTTPS) with the secret key (equivalent to service-role).
Keys come only from env / .env — never hardcoded; .env is gitignored.
Real usage: `from pipeline.supa import get_client; store = SupabaseStore(get_client())`
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_dotenv_if_present() -> None:
    if not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        # Strip matching surrounding quotes (KEY="value" / KEY='value').
        # Don't parse inline comments: secret keys / URLs may contain '#', and truncating
        # on '#' would corrupt the value.
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k:
            os.environ.setdefault(k, v)


@lru_cache(maxsize=1)
def get_client():
    """Build and cache the Supabase client. Raise a clear error if the keys are missing."""
    _load_dotenv_if_present()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL / SUPABASE_SECRET_KEY (expected in system1-app/.env or env vars)")
    from supabase import create_client
    return create_client(url, key)
