import { useEffect, useState } from "react";

/** Re-renders once per second while mounted. */
export function useTicker(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

/** Milliseconds remaining until targetMs (never negative). */
export function useRemainingMs(targetMs: number): number {
  const now = useTicker();
  return Math.max(0, targetMs - now);
}
