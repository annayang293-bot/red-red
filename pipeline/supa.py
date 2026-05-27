"""Supabase 客户端工厂 —— 从环境变量 / `.env` 读 URL + secret key。

数据读写走 PostgREST(HTTPS),用 secret key(service-role 等价)。
密钥只从环境/.env 读,绝不硬编码;.env 已 gitignore。
真用法:`from pipeline.supa import get_client; store = SupabaseStore(get_client())`
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
        # 去掉成对包裹引号(KEY="value" / KEY='value')。
        # 不解析行内注释:secret key / URL 里可能含 '#',按 '#' 截断会损坏值。
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k:
            os.environ.setdefault(k, v)


@lru_cache(maxsize=1)
def get_client():
    """构建并缓存 Supabase client。缺密钥则抛清晰错误。"""
    _load_dotenv_if_present()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError(
            "缺 SUPABASE_URL / SUPABASE_SECRET_KEY(应在 system1-app/.env 或环境变量里)")
    from supabase import create_client
    return create_client(url, key)
