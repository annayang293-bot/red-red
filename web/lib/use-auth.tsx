/**
 * Auth context for the app (System ① BYOK, Phase 0-B).
 *
 * Tracks the current Supabase session client-side: reads it once on mount, then stays in sync via
 * onAuthStateChange (login, logout, token refresh). Wrap the app in <AuthProvider> (see _app.tsx)
 * and read state with useAuth().
 *
 * `configError` is true when the public env vars are missing — so the UI can show a clear message
 * instead of crashing before the anon key has been added.
 */
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import type { Session, User } from "@supabase/supabase-js";
import { getSupabaseBrowser, hasSupabaseBrowserConfig } from "./supabase-browser";

interface AuthState {
  session: Session | null;
  user: User | null;
  loading: boolean;
  configError: boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  // NEXT_PUBLIC_* are inlined at build → config presence is static. Derive it in render (no effect /
  // no synchronous setState-in-effect, which the react-hooks lint rule forbids).
  const configured = hasSupabaseBrowserConfig();
  const [session, setSession] = useState<Session | null>(null);
  // Only "loading" when we will actually fetch a session; unconfigured resolves immediately.
  const [loading, setLoading] = useState(configured);

  useEffect(() => {
    if (!configured) return; // nothing to load; loading is already false
    // supabase-js v2 fires an INITIAL_SESSION event right after subscribing, so onAuthStateChange
    // alone covers the initial session load — no separate getSession() (which would be a redundant
    // race that settles to the same value).
    const sb = getSupabaseBrowser();
    const { data: sub } = sb.auth.onAuthStateChange((_event, s) => {
      setSession(s);
      setLoading(false);
    });
    return () => sub.subscription.unsubscribe();
  }, [configured]);

  const signOut = async () => {
    if (hasSupabaseBrowserConfig()) await getSupabaseBrowser().auth.signOut();
  };

  return (
    <AuthContext.Provider
      value={{ session, user: session?.user ?? null, loading, configError: !configured, signOut }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
