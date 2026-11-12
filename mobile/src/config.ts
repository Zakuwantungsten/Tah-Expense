import Constants from "expo-constants";

const extra = (Constants.expoConfig?.extra ?? {}) as {
  apiBaseUrl?: string;
};

/** Production default; override with EXPO_PUBLIC_API_BASE_URL for local/LAN. */
export const API_BASE_URL = (
  process.env.EXPO_PUBLIC_API_BASE_URL ||
  extra.apiBaseUrl ||
  "https://api.meedexpe.com"
).replace(/\/$/, "");
