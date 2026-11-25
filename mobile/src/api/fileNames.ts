/** Prefer human original names over ImagePicker / cache UUID basenames. */

const UUID_FILE_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(\.[a-z0-9]+)?$/i;
const HEX32_FILE_RE = /^[0-9a-f]{32}(\.[a-z0-9]+)?$/i;

export function basenameOf(pathOrName: string): string {
  const cleaned = String(pathOrName || "")
    .replace(/\\/g, "/")
    .split("?")[0];
  const parts = cleaned.split("/").filter(Boolean);
  return parts[parts.length - 1] || cleaned;
}

/** True when the name looks like an OS / Expo cache id, not a user filename. */
export function looksGeneratedFileName(name: string): boolean {
  const base = basenameOf(name);
  if (!base) return true;
  return UUID_FILE_RE.test(base) || HEX32_FILE_RE.test(base);
}

export function guessNameFromUri(uri: string, fallback: string): string {
  const last = basenameOf(uri);
  if (last.includes(".") && !looksGeneratedFileName(last)) {
    return last;
  }
  return fallback;
}

/**
 * Choose the filename to store as ``original_filename`` on the server.
 * Skips UUID / hex cache names from camera / ImagePicker URIs.
 */
export function pickOriginalFileName(opts: {
  fileName?: string | null;
  name?: string | null;
  uri: string;
  fallback: string;
}): string {
  for (const raw of [opts.fileName, opts.name]) {
    if (!raw) continue;
    const base = basenameOf(raw);
    if (base && !looksGeneratedFileName(base)) {
      return base;
    }
  }
  return guessNameFromUri(opts.uri, opts.fallback);
}

export function stampFallback(prefix: string, ext = ".jpg"): string {
  const safeExt = ext.startsWith(".") ? ext : `.${ext}`;
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}` +
    `-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `${prefix}-${stamp}${safeExt}`;
}
