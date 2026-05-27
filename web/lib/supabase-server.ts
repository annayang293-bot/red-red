/**
 * 服务端 Supabase client(只在 API 路由里 import,绝不进浏览器 bundle)。
 *
 * 用 secret key(service-role 等价)走 PostgREST。key 只在 server 端 process.env,
 * 本地放 web/.env.local(gitignored),Vercel 部署配同名环境变量(Step 8)。
 *
 * 懒构建:缺 key 时只在被调用那刻抛错,不在 import/build 期炸(CI build 无 key 也能过)。
 */
import { createClient, SupabaseClient } from "@supabase/supabase-js";

let _client: SupabaseClient | null = null;

export function getSupabaseAdmin(): SupabaseClient {
  if (_client) return _client;
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!url || !key) {
    throw new Error(
      "缺 SUPABASE_URL / SUPABASE_SECRET_KEY(本地放 web/.env.local;Vercel 配环境变量)"
    );
  }
  _client = createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return _client;
}
