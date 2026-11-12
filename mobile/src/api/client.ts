import { API_BASE_URL } from "../config";
import { clearTokens, getAccessToken, getRefreshToken, saveTokens } from "../auth/tokens";

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  access_expires_at?: string;
  refresh_expires_at?: string;
  token_type?: string;
};

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  formData?: FormData;
  auth?: boolean;
  retry?: boolean;
  /** Override default 20s (uploads need longer on slow hotspot). */
  timeoutMs?: number;
};

let refreshInFlight: Promise<boolean> | null = null;

const DEFAULT_TIMEOUT_MS = 20_000;

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs = DEFAULT_TIMEOUT_MS
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (exc) {
    if (exc instanceof Error && exc.name === "AbortError") {
      throw new ApiError(
        0,
        "timeout",
        init.body instanceof FormData
          ? "Upload timed out. Try a smaller photo, or stay on the hotspot."
          : `Timed out talking to ${API_BASE_URL}.`
      );
    }
    const detail = exc instanceof Error ? exc.message : "network error";
    // Android often reports bad file URIs as "Network request failed".
    if (init.body instanceof FormData) {
      throw new ApiError(
        0,
        "upload_network",
        `Upload failed (${detail}). If login works, this is usually the photo URI — try Photos again after reload.`
      );
    }
    throw new ApiError(
      0,
      "network",
      `Cannot reach ${API_BASE_URL} (${detail}). Same network as the PC?`
    );
  } finally {
    clearTimeout(timer);
  }
}

async function refreshOnce(): Promise<boolean> {
  const refresh = await getRefreshToken();
  if (!refresh) {
    await clearTokens();
    return false;
  }
  let response: Response;
  try {
    response = await fetchWithTimeout(`${API_BASE_URL}/v1/mobile/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
  } catch {
    // Network blip — keep tokens so cold start offline does not force re-login.
    return false;
  }
  if (!response.ok) {
    await clearTokens();
    return false;
  }
  const tokens = (await response.json()) as TokenPair;
  await saveTokens(tokens);
  return true;
}

async function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = refreshOnce().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

/** Public bootstrap helper — renew access from stored refresh token. */
export async function tryRefreshSession(): Promise<boolean> {
  return refreshSession();
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (!options.formData) {
    headers["Content-Type"] = "application/json";
  }
  if (options.auth !== false) {
    const access = await getAccessToken();
    if (access) {
      headers.Authorization = `Bearer ${access}`;
    }
  }

  const response = await fetchWithTimeout(
    `${API_BASE_URL}/${path.replace(/^\//, "")}`,
    {
      method: options.method || (options.body || options.formData ? "POST" : "GET"),
      headers,
      body: options.formData
        ? options.formData
        : options.body !== undefined
          ? JSON.stringify(options.body)
          : undefined,
    },
    options.timeoutMs
  );

  if (response.status === 401 && options.auth !== false && options.retry !== false) {
    const ok = await refreshSession();
    if (ok) {
      return apiRequest<T>(path, { ...options, retry: false });
    }
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { error: { message: text } };
    }
  }

  if (!response.ok) {
    const err = (payload as { error?: { code?: string; message?: string } })?.error;
    throw new ApiError(
      response.status,
      err?.code || "request_failed",
      err?.message || `Request failed (${response.status})`
    );
  }
  return payload as T;
}
