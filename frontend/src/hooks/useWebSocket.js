import { useEffect, useRef, useState, useCallback } from "react";

const MAX_RECONNECT_DELAY = 30000;
const INITIAL_RECONNECT_DELAY = 1000;

export default function useWebSocket() {
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState(null);
  const wsRef = useRef(null);
  const reconnectDelay = useRef(INITIAL_RECONNECT_DELAY);
  const reconnectTimer = useRef(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      reconnectDelay.current = INITIAL_RECONNECT_DELAY;
      // #region agent log
      fetch('http://127.0.0.1:7458/ingest/b530fd28-deaa-4c3d-9cd6-e49423133f3b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'5d0c12'},body:JSON.stringify({sessionId:'5d0c12',location:'useWebSocket.js:onopen',message:'WebSocket connected',data:{url},timestamp:Date.now(),hypothesisId:'H1'})}).catch(()=>{});
      // #endregion
    };

    ws.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data);
        // #region agent log
        if (parsed.type && parsed.type.startsWith('scan')) {
          fetch('http://127.0.0.1:7458/ingest/b530fd28-deaa-4c3d-9cd6-e49423133f3b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'5d0c12'},body:JSON.stringify({sessionId:'5d0c12',location:'useWebSocket.js:onmessage',message:'WS scan event received',data:{type:parsed.type,payload:parsed.payload},timestamp:Date.now(),hypothesisId:'H1'})}).catch(()=>{});
        }
        // #endregion
        setLastMessage(parsed);
      } catch {
        /* ignore non-JSON */
      }
    };

    ws.onclose = (ev) => {
      // #region agent log
      fetch('http://127.0.0.1:7458/ingest/b530fd28-deaa-4c3d-9cd6-e49423133f3b',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'5d0c12'},body:JSON.stringify({sessionId:'5d0c12',location:'useWebSocket.js:onclose',message:'WebSocket closed',data:{code:ev.code,reason:ev.reason},timestamp:Date.now(),hypothesisId:'H1'})}).catch(()=>{});
      // #endregion
      setConnected(false);
      wsRef.current = null;
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(
          reconnectDelay.current * 2,
          MAX_RECONNECT_DELAY,
        );
        connect();
      }, reconnectDelay.current);
    };

    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  return { connected, lastMessage };
}
