"use client";

import { useEffect, useRef, useState } from "react";

import { wsUrl } from "@/lib/api";
import type { LiveEvent } from "@/types";

const MAX_EVENTS = 50;

/** Subscribe to the server's live memory event stream over WebSocket.
 *  Reconnects automatically with backoff. */
export function useLiveEvents() {
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const retryRef = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;

    const connect = () => {
      const url = wsUrl();
      if (!url) return;
      try {
        ws = new WebSocket(url);
      } catch {
        scheduleRetry();
        return;
      }
      ws.onopen = () => {
        retryRef.current = 0;
        setConnected(true);
      };
      ws.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (data.type === "event") {
            setEvents((prev) =>
              [{ event: data.event, payload: data.payload, receivedAt: Date.now() }, ...prev].slice(
                0,
                MAX_EVENTS
              )
            );
          }
        } catch {}
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) scheduleRetry();
      };
      ws.onerror = () => ws?.close();
    };

    const scheduleRetry = () => {
      const delay = Math.min(15000, 1000 * 2 ** retryRef.current);
      retryRef.current += 1;
      timer = setTimeout(connect, delay);
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);

  return { events, connected };
}
