/** Local upload history (device-side). Not clearable from the UI — permanent trail. */

import * as FileSystem from "expo-file-system/legacy";

export type UploadHistoryEntry = {
  id: string;
  at: string;
  kind: "request" | "early" | "bulk";
  requestId?: string | null;
  requestIds?: string[];
  itemLabel?: string;
  truckNumber?: string;
  sent: number;
  failed: number;
  status?: string;
  names: string[];
  fileIds?: string[];
  errors?: string[];
  partial?: boolean;
};

const FILE = `${FileSystem.documentDirectory || ""}tahmeed-upload-history.json`;
const MAX = 200;

async function readAll(): Promise<UploadHistoryEntry[]> {
  try {
    const info = await FileSystem.getInfoAsync(FILE);
    if (!info.exists) {
      return [];
    }
    const raw = await FileSystem.readAsStringAsync(FILE);
    const parsed = JSON.parse(raw) as UploadHistoryEntry[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

async function writeAll(entries: UploadHistoryEntry[]): Promise<void> {
  if (!FileSystem.documentDirectory) {
    return;
  }
  await FileSystem.writeAsStringAsync(FILE, JSON.stringify(entries.slice(0, MAX)));
}

export async function listUploadHistory(): Promise<UploadHistoryEntry[]> {
  return readAll();
}

export async function appendUploadHistory(
  entry: Omit<UploadHistoryEntry, "id" | "at"> & { at?: string }
): Promise<void> {
  const rows = await readAll();
  rows.unshift({
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    at: entry.at || new Date().toISOString(),
    kind: entry.kind,
    requestId: entry.requestId,
    requestIds: entry.requestIds,
    itemLabel: entry.itemLabel,
    truckNumber: entry.truckNumber,
    sent: entry.sent,
    failed: entry.failed,
    status: entry.status,
    names: (entry.names || []).slice(0, 20),
    fileIds: (entry.fileIds || []).filter(Boolean).slice(0, 40),
    errors: entry.errors?.slice(0, 10),
    partial: entry.partial,
  });
  await writeAll(rows);
}
