import { apiRequest, type TokenPair } from "./client";
import { clearTokens, saveTokens } from "../auth/tokens";

export async function login(username: string, password: string): Promise<TokenPair> {
  const tokens = await apiRequest<TokenPair>("v1/mobile/auth/login", {
    method: "POST",
    auth: false,
    body: { username, password },
  });
  await saveTokens(tokens);
  return tokens;
}

export async function logout(): Promise<void> {
  await clearTokens();
}

export async function registerDevice(expoPushToken: string): Promise<void> {
  await apiRequest("v1/mobile/devices", {
    method: "POST",
    body: { expo_push_token: expoPushToken },
  });
}
