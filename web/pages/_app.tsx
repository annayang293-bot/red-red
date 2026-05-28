import "@/styles/globals.css";
import type { AppProps } from "next/app";
import { LangContext, useLangState } from "@/lib/i18n";

export default function App({ Component, pageProps }: AppProps) {
  const lang = useLangState();
  return (
    <LangContext.Provider value={lang}>
      <Component {...pageProps} />
    </LangContext.Provider>
  );
}
