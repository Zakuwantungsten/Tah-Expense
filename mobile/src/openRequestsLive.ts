/**
 * Live open-request refresh bus: WebSocket when available, AppState poll fallback,
 * plus push/notification-triggered invalidation.
 */

import { AppState, type AppStateStatus } from "react-native";
import { API_BASE_URL } from "./config";
import { getAccessToken } from "./auth/tokens";

type Listener = () => void;

const listeners = new Set<Listener>();
let started = false;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let ws: WebSocket | null = null;
let wsRetryTimer: ReturnType<typeof setTimeout> | null = null;
let appStateSub: { remove: () => void } | null = null;

export function subscribeOpenRequestsLive(listener: Listener): () => void {
  listeners.add(listener);
  ensureLiveStarted();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      stopLive();
    }
  };
}

export function notifyOpenRequestsChanged(): void {
  for (const listener of Array.from(listeners)) {
    try {
      listener();
    } catch {
      // ignore subscriber errors
    }
  }
}

function ensureLiveStarted(): void {
  if (started) {
    return;
  }
  started = true;
  connectWs();
  startPoll();
  appStateSub = AppState.addEventListener("change", onAppState);
}

function stopLive(): void {
  started = false;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (wsRetryTimer) {
    clearTimeout(wsRetryTimer);
    wsRetryTimer = null;
  }
  if (ws) {
    try {
      ws.close();
    } catch {
      // ignore
    }
    ws = null;
  }
  appStateSub?.remove();
  appStateSub = null;
}

function onAppState(next: AppStateStatus): void {
  if (next === "active") {
    notifyOpenRequestsChanged();
    connectWs();
    startPoll();
  } else {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }
}

function startPoll(): void {
  if (pollTimer) {
    return;
  }
  // Fallback when WS is down / Expo Go flaky — keep list fresh without pull.
  pollTimer = setInterval(() => {
    if (AppState.currentState === "active") {
      notifyOpenRequestsChanged();
    }
  }, 12000);
}

function wsUrl(token: string): string {
  const base = API_BASE_URL.replace(/\/$/, "");
  const scheme = base.startsWith("https") ? "wss" : "ws";
  const host = base.replace(/^https?:\/\//, "");
  return `${scheme}://${host}/v1/mobile/ws/receipts?token=${encodeURIComponent(token)}`;
}

async function connectWs(): Promise<void> {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const token = await getAccessToken();
  if (!token) {
    return;
  }
  try {
    const socket = new WebSocket(wsUrl(token));
    ws = socket;
    socket.onmessage = () => {
      notifyOpenRequestsChanged();
    };
    socket.onclose = () => {
      ws = null;
      if (!started) {
        return;
      }
      if (wsRetryTimer) {
        clearTimeout(wsRetryTimer);
      }
      wsRetryTimer = setTimeout(() => {
        void connectWs();
      }, 5000);
    };
    socket.onerror = () => {
      try {
        socket.close();
      } catch {
        // ignore
      }
    };
  } catch {
    // Poll fallback covers this.
  }
}
