import { apiRequest } from "./client";

export type StorageHealth = {
  healthy: boolean;
  degraded: boolean;
  reason?: string | null;
  message?: string | null;
  whatsapp_fallback?: boolean;
  whatsapp_hint?: string | null;
  path?: string | null;
  checked_at?: string | null;
};

export async function fetchStorageHealth(): Promise<StorageHealth> {
  return apiRequest<StorageHealth>("v1/mobile/storage-health");
}

export function isStorageDegraded(health: StorageHealth | null | undefined): boolean {
  if (!health) {
    return false;
  }
  if (health.degraded) {
    return true;
  }
  return health.healthy === false;
}
