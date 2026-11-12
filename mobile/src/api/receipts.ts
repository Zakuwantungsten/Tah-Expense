import * as FileSystem from "expo-file-system/legacy";
import { getAccessToken, getRefreshToken, saveTokens, clearTokens } from "../auth/tokens";
import { API_BASE_URL } from "../config";
import { ApiError, apiRequest, type TokenPair } from "./client";
import { ensureUploadableUri } from "./uploadLocal";

export type ReceiptRequestSummary = {
  id: string;
  status: string;
  asked_of_name: string;
  opened_at?: string;
  last_reminded_at?: string | null;
  reminder_count?: number;
  file_ids?: string[];
  waiver_reason?: string | null;
  partial_receipt?: boolean;
  partial_note?: string;
  transaction: {
    id: string | null;
    date?: string;
    amount?: number;
    currency?: string;
    item?: string;
    payee?: string;
    ownership?: string;
    receipt_status?: string;
    description?: string;
    truck_number?: string;
  };
};

export type UploadResult = {
  request: ReceiptRequestSummary;
  files: Array<{ id: string; filename: string; size: number; stored_name?: string }>;
};

export type EarlyUploadResult = {
  files: Array<{ id: string; filename: string; size: number; stored_name?: string }>;
  total: number;
};

export type AtomicUploadSummary = {
  sent: Array<{ name: string; id?: string }>;
  failed: Array<{ name: string; error: string }>;
  request: ReceiptRequestSummary | null;
  files: Array<{ id: string; filename: string; size: number; stored_name?: string }>;
};

export async function listOpenRequests(): Promise<ReceiptRequestSummary[]> {
  const payload = await apiRequest<{ items: ReceiptRequestSummary[]; total: number }>(
    "v1/mobile/receipt-requests"
  );
  return payload.items || [];
}

export async function getRequest(id: string): Promise<ReceiptRequestSummary> {
  return apiRequest<ReceiptRequestSummary>(`v1/mobile/receipt-requests/${id}`);
}

async function refreshAccessToken(): Promise<string | null> {
  const refresh = await getRefreshToken();
  if (!refresh) {
    await clearTokens();
    return null;
  }
  const response = await fetch(`${API_BASE_URL}/v1/mobile/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!response.ok) {
    await clearTokens();
    return null;
  }
  const tokens = (await response.json()) as TokenPair;
  await saveTokens(tokens);
  return tokens.access_token;
}

function parseUploadBody(status: number, body: string): UploadResult {
  let payload: unknown = null;
  if (body) {
    try {
      payload = JSON.parse(body);
    } catch {
      payload = { error: { message: body } };
    }
  }
  if (status < 200 || status >= 300) {
    const err = (payload as { error?: { code?: string; message?: string } })?.error;
    throw new ApiError(
      status,
      err?.code || "upload_failed",
      err?.message || `Upload failed (${status})`
    );
  }
  return payload as UploadResult;
}

export type UploadProgressEvent = {
  /** Overall 0–1 across the whole batch */
  overall: number;
  /** 0–1 for the file currently uploading */
  fileProgress: number;
  fileIndex: number;
  fileCount: number;
  fileName: string;
};

type ProgressCb = (event: UploadProgressEvent) => void;

async function uploadWithProgress(
  url: string,
  localUri: string,
  file: { name: string; mimeType?: string | null },
  accessToken: string | null,
  onFileProgress?: (fraction: number) => void
): Promise<{ status: number; body: string }> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const options = {
    httpMethod: "POST" as const,
    uploadType: FileSystem.FileSystemUploadType.MULTIPART,
    fieldName: "files",
    mimeType: file.mimeType || "image/jpeg",
    headers,
  };
  if (typeof FileSystem.createUploadTask === "function" && onFileProgress) {
    const task = FileSystem.createUploadTask(
      url,
      localUri,
      options,
      (data) => {
        const expected = data.totalBytesExpectedToSend || 0;
        const sent = data.totalBytesSent || 0;
        const fraction = expected > 0 ? Math.min(1, sent / expected) : 0;
        onFileProgress(fraction);
      }
    );
    const response = await task.uploadAsync();
    return {
      status: response?.status ?? 0,
      body: response?.body ?? "",
    };
  }
  const response = await FileSystem.uploadAsync(url, localUri, options);
  onFileProgress?.(1);
  return { status: response.status, body: response.body };
}

async function uploadOneFile(
  requestId: string,
  localUri: string,
  file: { name: string; mimeType?: string | null },
  accessToken: string | null,
  onFileProgress?: (fraction: number) => void,
  opts?: { partial?: boolean; partialNote?: string }
): Promise<UploadResult> {
  const url = `${API_BASE_URL}/v1/mobile/receipt-requests/${requestId}/files`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const parameters: Record<string, string> = {};
  if (opts?.partial) {
    parameters.partial = "true";
    parameters.partial_note = (opts.partialNote || "").trim();
  }
  const options = {
    httpMethod: "POST" as const,
    uploadType: FileSystem.FileSystemUploadType.MULTIPART,
    fieldName: "files",
    mimeType: file.mimeType || "image/jpeg",
    headers,
    parameters: Object.keys(parameters).length ? parameters : undefined,
  };
  if (typeof FileSystem.createUploadTask === "function" && onFileProgress) {
    const task = FileSystem.createUploadTask(
      url,
      localUri,
      options,
      (data) => {
        const expected = data.totalBytesExpectedToSend || 0;
        const sent = data.totalBytesSent || 0;
        const fraction = expected > 0 ? Math.min(1, sent / expected) : 0;
        onFileProgress(fraction);
      }
    );
    const response = await task.uploadAsync();
    return parseUploadBody(response?.status ?? 0, response?.body ?? "");
  }
  const response = await FileSystem.uploadAsync(url, localUri, options);
  onFileProgress?.(1);
  return parseUploadBody(response.status, response.body);
}

function parseEarlyBody(status: number, body: string): EarlyUploadResult {
  let payload: unknown = null;
  if (body) {
    try {
      payload = JSON.parse(body);
    } catch {
      payload = { error: { message: body } };
    }
  }
  if (status < 200 || status >= 300) {
    const err = (payload as { error?: { code?: string; message?: string } })?.error;
    throw new ApiError(
      status,
      err?.code || "upload_failed",
      err?.message || `Upload failed (${status})`
    );
  }
  return payload as EarlyUploadResult;
}

async function uploadOneEarlyFile(
  localUri: string,
  file: { name: string; mimeType?: string | null },
  accessToken: string | null,
  onFileProgress?: (fraction: number) => void
): Promise<EarlyUploadResult> {
  const url = `${API_BASE_URL}/v1/mobile/receipt-files`;
  const response = await uploadWithProgress(
    url,
    localUri,
    file,
    accessToken,
    onFileProgress
  );
  return parseEarlyBody(response.status, response.body);
}

function errMessage(exc: unknown): string {
  if (exc instanceof ApiError) {
    return exc.message;
  }
  if (exc instanceof Error) {
    return exc.message;
  }
  return "Upload failed";
}

/** Park photos/files before a ledger row exists — continues on per-file failure. */
export async function uploadEarlyFiles(
  files: Array<{ uri: string; name: string; mimeType?: string | null }>,
  onProgress?: ProgressCb
): Promise<AtomicUploadSummary> {
  if (!files.length) {
    throw new ApiError(400, "empty", "Pick at least one file first");
  }
  let access = await getAccessToken();
  const summary: AtomicUploadSummary = {
    sent: [],
    failed: [],
    request: null,
    files: [],
  };
  const total = files.length;
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const emit = (fileProgress: number) => {
      onProgress?.({
        overall: (i + fileProgress) / total,
        fileProgress,
        fileIndex: i,
        fileCount: total,
        fileName: file.name,
      });
    };
    try {
      const localUri = await ensureUploadableUri(file.uri, file.name);
      let result: EarlyUploadResult;
      try {
        result = await uploadOneEarlyFile(localUri, file, access, emit);
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) {
          access = await refreshAccessToken();
          if (!access) {
            throw new ApiError(401, "unauthorized", "Session expired. Sign in again.");
          }
          result = await uploadOneEarlyFile(localUri, file, access, emit);
        } else {
          throw exc;
        }
      }
      const got = result.files || [];
      summary.files.push(...got);
      if (got.length) {
        for (const f of got) {
          summary.sent.push({ name: f.filename || file.name, id: f.id });
        }
      } else {
        summary.sent.push({ name: file.name });
      }
      emit(1);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 401) {
        throw exc;
      }
      summary.failed.push({ name: file.name, error: errMessage(exc) });
      emit(1);
    }
  }
  return summary;
}

export type UploadFilesOptions = {
  partial?: boolean;
  partialNote?: string;
  onProgress?: ProgressCb;
};

/**
 * Atomic multi-file upload — each file is attempted; failures do not stop the rest.
 * Expo FileSystem multipart (RN fetch FormData is unreliable on Expo Go).
 */
export async function uploadFiles(
  requestId: string,
  files: Array<{ uri: string; name: string; mimeType?: string | null }>,
  onProgressOrOpts?: ProgressCb | UploadFilesOptions
): Promise<AtomicUploadSummary> {
  if (!files.length) {
    throw new ApiError(400, "empty", "Pick at least one file first");
  }

  const opts: UploadFilesOptions =
    typeof onProgressOrOpts === "function"
      ? { onProgress: onProgressOrOpts }
      : onProgressOrOpts || {};
  const onProgress = opts.onProgress;
  const partial = Boolean(opts.partial);
  const partialNote = (opts.partialNote || "").trim();

  let access = await getAccessToken();
  const summary: AtomicUploadSummary = {
    sent: [],
    failed: [],
    request: null,
    files: [],
  };

  const total = files.length;
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const emit = (fileProgress: number) => {
      onProgress?.({
        overall: (i + fileProgress) / total,
        fileProgress,
        fileIndex: i,
        fileCount: total,
        fileName: file.name,
      });
    };
    try {
      const localUri = await ensureUploadableUri(file.uri, file.name);
      let result: UploadResult;
      try {
        result = await uploadOneFile(requestId, localUri, file, access, emit, {
          partial,
          partialNote,
        });
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) {
          access = await refreshAccessToken();
          if (!access) {
            throw new ApiError(401, "unauthorized", "Session expired. Sign in again.");
          }
          result = await uploadOneFile(requestId, localUri, file, access, emit, {
            partial,
            partialNote,
          });
        } else {
          throw exc;
        }
      }
      const got = result.files || [];
      summary.files.push(...got);
      summary.request = result.request;
      if (got.length) {
        for (const f of got) {
          summary.sent.push({ name: f.filename || file.name, id: f.id });
        }
      } else {
        summary.sent.push({ name: file.name });
      }
      emit(1);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 401) {
        throw exc;
      }
      summary.failed.push({ name: file.name, error: errMessage(exc) });
      emit(1);
    }
  }

  if (!summary.sent.length && summary.failed.length) {
    throw new ApiError(
      422,
      "upload_all_failed",
      summary.failed.map((f) => `${f.name}: ${f.error}`).join("\n")
    );
  }
  return summary;
}

/** One receipt (file set) applied to many open requests. */
export async function uploadFilesToManyRequests(
  requestIds: string[],
  files: Array<{ uri: string; name: string; mimeType?: string | null }>,
  opts?: UploadFilesOptions
): Promise<{
  ok: string[];
  failed: Array<{ requestId: string; error: string }>;
  sentNames: string[];
  fileIds: string[];
}> {
  const ids = Array.from(new Set(requestIds.filter(Boolean)));
  if (!ids.length) {
    throw new ApiError(400, "empty", "Select at least one request");
  }
  const ok: string[] = [];
  const failed: Array<{ requestId: string; error: string }> = [];
  const sentNames: string[] = [];
  const fileIds: string[] = [];
  const total = ids.length;
  for (let i = 0; i < ids.length; i++) {
    const requestId = ids[i];
    try {
      const summary = await uploadFiles(requestId, files, {
        ...opts,
        onProgress: (ev) => {
          opts?.onProgress?.({
            ...ev,
            overall: (i + ev.overall) / total,
            fileIndex: i,
            fileCount: total,
            fileName: `${requestId.slice(-6)} · ${ev.fileName}`,
          });
        },
      });
      ok.push(requestId);
      for (const s of summary.sent) {
        if (s.name && !sentNames.includes(s.name)) {
          sentNames.push(s.name);
        }
        if (s.id) {
          fileIds.push(s.id);
        }
      }
    } catch (exc) {
      failed.push({ requestId, error: errMessage(exc) });
    }
  }
  return { ok, failed, sentNames, fileIds };
}

export async function downloadReceiptFile(
  fileId: string
): Promise<{ uri: string; filename: string; contentType: string }> {
  let access = await getAccessToken();
  const metaUrl = `${API_BASE_URL}/v1/mobile/receipt-files/${fileId}/meta`;
  const openUrl = `${API_BASE_URL}/v1/mobile/receipt-files/${fileId}/open`;

  async function fetchMeta(token: string | null) {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    const res = await fetch(metaUrl, { headers });
    if (res.status === 401) {
      throw new ApiError(401, "unauthorized", "Session expired");
    }
    if (!res.ok) {
      throw new ApiError(res.status, "meta_failed", "Could not load file info");
    }
    return (await res.json()) as {
      original_filename?: string;
      content_type?: string;
    };
  }

  let meta: { original_filename?: string; content_type?: string };
  try {
    meta = await fetchMeta(access);
  } catch (exc) {
    if (exc instanceof ApiError && exc.status === 401) {
      access = await refreshAccessToken();
      if (!access) {
        throw new ApiError(401, "unauthorized", "Session expired. Sign in again.");
      }
      meta = await fetchMeta(access);
    } else {
      throw exc;
    }
  }

  const filename =
    (meta.original_filename || `receipt-${fileId}`).replace(/[\\/:*?"<>|]+/g, "_") ||
    `receipt-${fileId}`;
  const dest = `${FileSystem.cacheDirectory || FileSystem.documentDirectory || ""}${filename}`;
  const headers: Record<string, string> = {};
  if (access) {
    headers.Authorization = `Bearer ${access}`;
  }
  const result = await FileSystem.downloadAsync(openUrl, dest, { headers });
  if (result.status < 200 || result.status >= 300) {
    throw new ApiError(result.status, "download_failed", "Could not download receipt");
  }
  return {
    uri: result.uri,
    filename,
    contentType: meta.content_type || result.headers?.["Content-Type"] || "application/octet-stream",
  };
}

export function formatUploadSummary(summary: AtomicUploadSummary): string {
  const lines = [
    `Sent: ${summary.sent.length}`,
    `Failed: ${summary.failed.length}`,
  ];
  if (summary.request?.status) {
    lines.push(`Request status: ${summary.request.status}`);
  }
  if (summary.failed.length) {
    lines.push("");
    for (const f of summary.failed.slice(0, 5)) {
      lines.push(`• ${f.name}: ${f.error}`);
    }
  }
  return lines.join("\n");
}

export async function waiveRequest(
  requestId: string,
  reason: string
): Promise<ReceiptRequestSummary> {
  return apiRequest<ReceiptRequestSummary>(`v1/mobile/receipt-requests/${requestId}/waive`, {
    method: "POST",
    body: { reason },
  });
}
