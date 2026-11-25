/** Copy picker URIs to a real file:// path whose basename is the original name. */

import * as FileSystem from "expo-file-system/legacy";

import { basenameOf, looksGeneratedFileName } from "./fileNames";

function safeName(name: string): string {
  const cleaned = basenameOf(name).replace(/[^a-zA-Z0-9._-]/g, "_");
  return cleaned || `file-${Date.now()}.bin`;
}

/**
 * Ensure a ``file://`` path whose basename matches *name*.
 *
 * Expo ``uploadAsync`` / multipart uses the URI basename as the uploaded
 * filename — so camera cache URIs like ``…/9f9483d8-….jpeg`` would otherwise
 * become ``original_filename`` on the server. Always copy when the URI name
 * differs from the intended original (or looks generated).
 */
export async function ensureUploadableUri(
  uri: string,
  name: string
): Promise<string> {
  const base = FileSystem.cacheDirectory;
  if (!base) {
    throw new Error("No cache directory available for upload");
  }
  const destName = safeName(name);
  const uriBase = basenameOf(uri);
  if (
    uri.startsWith("file://") &&
    uriBase === destName &&
    !looksGeneratedFileName(uriBase)
  ) {
    return uri;
  }
  const dest = `${base}receipt-upload-${Date.now()}-${destName}`;
  await FileSystem.copyAsync({ from: uri, to: dest });
  return dest;
}
