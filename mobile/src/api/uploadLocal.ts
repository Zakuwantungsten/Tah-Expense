/** Copy picker URIs to a real file:// path Android FormData can upload. */

import * as FileSystem from "expo-file-system/legacy";

function safeName(name: string): string {
  const cleaned = name.replace(/[^a-zA-Z0-9._-]/g, "_");
  return cleaned || `file-${Date.now()}.bin`;
}

export async function ensureUploadableUri(
  uri: string,
  name: string
): Promise<string> {
  if (uri.startsWith("file://")) {
    return uri;
  }
  const base = FileSystem.cacheDirectory;
  if (!base) {
    throw new Error("No cache directory available for upload");
  }
  const dest = `${base}receipt-upload-${Date.now()}-${safeName(name)}`;
  await FileSystem.copyAsync({ from: uri, to: dest });
  return dest;
}
