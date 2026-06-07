/**
 * Subscribe to an SSE channel and feed messages into a callback.
 * Auto-reconnects with exponential backoff.
 */

import { useEffect, useRef } from "react"

export function useSseStream<T = unknown>(
  url: string,
  onMessage: (msg: T) => void,
  opts: { enabled?: boolean } = {},
) {
  const enabled = opts.enabled ?? true
  const onMessageRef = useRef(onMessage)
  onMessageRef.current = onMessage

  useEffect(() => {
    if (!enabled) return
    let es: EventSource | null = null
    let cancelled = false
    let backoff = 500

    const open = () => {
      if (cancelled) return
      es = new EventSource(url)
      es.onmessage = (e) => {
        try {
          onMessageRef.current(JSON.parse(e.data) as T)
        } catch { /* skip malformed */ }
      }
      // sse-starlette uses custom event names; listen to a few:
      const handler = (e: MessageEvent) => {
        try { onMessageRef.current(JSON.parse(e.data) as T) }
        catch { /* skip */ }
      }
      es.addEventListener("event", handler as EventListener)
      es.addEventListener("session_update", handler as EventListener)
      es.onerror = () => {
        es?.close()
        if (cancelled) return
        setTimeout(open, backoff)
        backoff = Math.min(backoff * 2, 15_000)
      }
      es.onopen = () => { backoff = 500 }
    }

    open()
    return () => {
      cancelled = true
      es?.close()
    }
  }, [url, enabled])
}
