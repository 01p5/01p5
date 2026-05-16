import { useEffect, useRef } from "react";

/**
 * Subscribe to an SSE endpoint and run a handler on every message.
 * Reconnects with a 2s backoff if the stream errors.
 *
 * The handler is captured in a ref so callers can pass an inline arrow
 * without forcing reconnects on every render.
 */
export function useSSE<T = unknown>(
  url: string | null,
  onMessage: (data: T) => void,
): void {
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = (): void => {
      if (cancelled) return;
      es = new EventSource(url);
      es.onmessage = (e: MessageEvent<string>) => {
        try {
          handlerRef.current(JSON.parse(e.data) as T);
        } catch { /* malformed frame, ignore */ }
      };
      es.onerror = () => {
        es?.close();
        es = null;
        if (!cancelled) {
          reconnectTimer = setTimeout(connect, 2000);
        }
      };
    };
    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    };
  }, [url]);
}
