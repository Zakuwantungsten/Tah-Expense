import * as SecureStore from "expo-secure-store";

const KEY = "tahmeed.notification_inbox.v1";
const MAX = 80;

export type InboxNotification = {
  id: string;
  title: string;
  body: string;
  requestId?: string | null;
  kind?: string | null;
  at: string;
  read: boolean;
};

async function readAll(): Promise<InboxNotification[]> {
  try {
    const raw = await SecureStore.getItemAsync(KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as InboxNotification[]) : [];
  } catch {
    return [];
  }
}

async function writeAll(items: InboxNotification[]): Promise<void> {
  await SecureStore.setItemAsync(KEY, JSON.stringify(items.slice(0, MAX)));
}

export async function listInboxNotifications(): Promise<InboxNotification[]> {
  return readAll();
}

export async function unreadInboxCount(): Promise<number> {
  const items = await readAll();
  return items.reduce((n, row) => n + (row.read ? 0 : 1), 0);
}

export async function appendInboxNotification(
  entry: Omit<InboxNotification, "id" | "at" | "read"> & {
    id?: string;
    at?: string;
    read?: boolean;
  }
): Promise<InboxNotification> {
  const items = await readAll();
  const row: InboxNotification = {
    id: entry.id || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title: entry.title || "Notification",
    body: entry.body || "",
    requestId: entry.requestId || null,
    kind: entry.kind || null,
    at: entry.at || new Date().toISOString(),
    read: Boolean(entry.read),
  };
  // Dedupe identical request+title within a short window.
  const next = [
    row,
    ...items.filter(
      (x) =>
        !(
          x.requestId &&
          row.requestId &&
          x.requestId === row.requestId &&
          x.title === row.title
        )
    ),
  ];
  await writeAll(next);
  return row;
}

export async function markInboxRead(id?: string): Promise<void> {
  const items = await readAll();
  const next = items.map((row) =>
    !id || row.id === id ? { ...row, read: true } : row
  );
  await writeAll(next);
}

export async function clearInbox(): Promise<void> {
  await SecureStore.deleteItemAsync(KEY);
}
