// v2 test and live mode. The dashboard shows one mode at a time; the switch in the sidebar
// sets it, and pages that read payments, balances, entries or payouts send it as `mode=`.
// The choice is a per-browser convenience, so it lives in localStorage (and falls back to
// test if storage is blocked).
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export type Mode = "test" | "live";

const KEY = "securepay.dashboard.mode";

function stored(): Mode {
  try {
    return window.localStorage.getItem(KEY) === "live" ? "live" : "test";
  } catch {
    return "test";
  }
}

interface ModeState {
  mode: Mode;
  setMode: (mode: Mode) => void;
}

const ModeContext = createContext<ModeState>({ mode: "test", setMode: () => undefined });

export function ModeProvider({ children }: { children: ReactNode }) {
  const [mode, setState] = useState<Mode>(stored);
  const setMode = useCallback((next: Mode) => {
    setState(next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      // storage blocked: the switch still works for this visit
    }
  }, []);
  const value = useMemo(() => ({ mode, setMode }), [mode, setMode]);
  return <ModeContext.Provider value={value}>{children}</ModeContext.Provider>;
}

export function useMode(): ModeState {
  return useContext(ModeContext);
}
