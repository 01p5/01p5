import { useEffect, useState, useRef } from "react";

/**
 * Poll an async function on an interval. Returns the latest value,
 * loading state, and any error. Pass `pause=true` to suspend polling.
 */
export function usePolling<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  pause = false,
): { data: T | null; error: Error | null; loading: boolean; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (pause) return;
    let cancelled = false;
    const run = async (): Promise<void> => {
      setLoading(true);
      try {
        const v = await fnRef.current();
        if (!cancelled) {
          setData(v);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void run();
    const id = setInterval(run, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs, pause, tick]);

  return { data, error, loading, refresh: () => setTick((t) => t + 1) };
}
