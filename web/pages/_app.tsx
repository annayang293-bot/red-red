import "@/styles/globals.css";
import type { AppProps } from "next/app";
import { LangContext, useLangState } from "@/lib/i18n";
import { AuthProvider, useAuth } from "@/lib/use-auth";
import LoginScreen from "@/components/LoginScreen";

// Gate every page behind auth (System ① BYOK, Phase 0-B). API routes are NOT affected — _app only
// wraps UI pages — so the cron endpoint, /api/run, etc. keep working unauthenticated as before.
function AuthGate({ children }: { children: React.ReactNode }) {
  const { loading, session, configError } = useAuth();

  if (configError) {
    return (
      <div className="min-h-screen flex items-center justify-center px-4 text-center">
        <div className="max-w-sm text-sm text-neutral-500">
          登录未配置:缺少 <code>NEXT_PUBLIC_SUPABASE_URL</code> /{" "}
          <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code>。
          <br />
          在 web/.env.local(本地)和 Vercel 环境变量(线上)里补上 anon key 即可。
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-sm text-neutral-400">
        加载中…
      </div>
    );
  }

  if (!session) return <LoginScreen />;

  return <>{children}</>;
}

export default function App({ Component, pageProps }: AppProps) {
  const lang = useLangState();
  return (
    <LangContext.Provider value={lang}>
      <AuthProvider>
        <AuthGate>
          <Component {...pageProps} />
        </AuthGate>
      </AuthProvider>
    </LangContext.Provider>
  );
}
