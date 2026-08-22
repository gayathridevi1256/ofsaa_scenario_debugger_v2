"use client";

import { useRef, useCallback } from "react";

const WS_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/^http/, "ws");

export function useWebSocket(jobId, handlers = {}) {
  const wsRef = useRef(null);
  const reconnectCount = useRef(0);
  const intentionalClose = useRef(false);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    intentionalClose.current = false;
    const ws = new WebSocket(`${WS_BASE}/api/ws/${jobId}`);
    wsRef.current = ws;

    ws.onopen = () => {
      handlers.onOpen?.();
      reconnectCount.current = 0;
    };

    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      handlers.onMessage?.(msg);
    };

    ws.onclose = () => {
      if (intentionalClose.current) { handlers.onClose?.(); return; }
      if (reconnectCount.current < 5) {
        reconnectCount.current += 1;
        setTimeout(connect, reconnectCount.current * 2000);
      } else {
        handlers.onError?.();
      }
    };
  }, [jobId, handlers]);

  const close = useCallback(() => {
    intentionalClose.current = true;
    wsRef.current?.close();
  }, []);

  return { connect, close, wsRef };
}
