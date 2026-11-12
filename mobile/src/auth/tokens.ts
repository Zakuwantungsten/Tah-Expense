import * as SecureStore from "expo-secure-store";
import type { TokenPair } from "../api/client";

const ACCESS_KEY = "tahmeed.access_token";
const REFRESH_KEY = "tahmeed.refresh_token";

export async function saveTokens(tokens: TokenPair): Promise<void> {
  await SecureStore.setItemAsync(ACCESS_KEY, tokens.access_token);
  await SecureStore.setItemAsync(REFRESH_KEY, tokens.refresh_token);
}

export async function getAccessToken(): Promise<string | null> {
  return SecureStore.getItemAsync(ACCESS_KEY);
}

export async function getRefreshToken(): Promise<string | null> {
  return SecureStore.getItemAsync(REFRESH_KEY);
}

export async function clearTokens(): Promise<void> {
  await SecureStore.deleteItemAsync(ACCESS_KEY);
  await SecureStore.deleteItemAsync(REFRESH_KEY);
}

export async function hasStoredSession(): Promise<boolean> {
  const access = await getAccessToken();
  const refresh = await getRefreshToken();
  return Boolean(access || refresh);
}
