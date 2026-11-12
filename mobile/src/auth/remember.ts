/** On-device remember-me prefs (SecureStore). */

import * as SecureStore from "expo-secure-store";

const REMEMBER_FLAG = "tahmeed.remember_me";
const REMEMBER_USER = "tahmeed.remember_username";
const REMEMBER_PASS = "tahmeed.remember_password";
const LAST_USER = "tahmeed.last_username";

export type RememberedCredentials = {
  remember: boolean;
  username: string;
  password: string;
};

export async function loadRemembered(): Promise<RememberedCredentials> {
  const [flag, user, pass, last] = await Promise.all([
    SecureStore.getItemAsync(REMEMBER_FLAG),
    SecureStore.getItemAsync(REMEMBER_USER),
    SecureStore.getItemAsync(REMEMBER_PASS),
    SecureStore.getItemAsync(LAST_USER),
  ]);
  const remember = flag === "1";
  return {
    remember,
    username: remember ? user || "" : last || "",
    password: remember ? pass || "" : "",
  };
}

export async function saveRemembered(
  remember: boolean,
  username: string,
  password: string
): Promise<void> {
  const user = username.trim();
  await SecureStore.setItemAsync(LAST_USER, user);
  if (remember && user && password) {
    await SecureStore.setItemAsync(REMEMBER_FLAG, "1");
    await SecureStore.setItemAsync(REMEMBER_USER, user);
    await SecureStore.setItemAsync(REMEMBER_PASS, password);
    return;
  }
  await SecureStore.setItemAsync(REMEMBER_FLAG, "0");
  await SecureStore.deleteItemAsync(REMEMBER_USER);
  await SecureStore.deleteItemAsync(REMEMBER_PASS);
}

export async function clearRememberedPassword(): Promise<void> {
  await SecureStore.deleteItemAsync(REMEMBER_PASS);
}
