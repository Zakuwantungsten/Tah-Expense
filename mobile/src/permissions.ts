/** Camera / photos permission helpers for Expo Go. */

import * as ImagePicker from "expo-image-picker";
import { Alert, Linking, Platform } from "react-native";

async function explainAndOpenSettings(title: string, message: string): Promise<void> {
  Alert.alert(title, message, [
    { text: "Cancel", style: "cancel" },
    {
      text: "Open settings",
      onPress: () => {
        void Linking.openSettings();
      },
    },
  ]);
}

/** Returns true when the camera may be opened. */
export async function ensureCameraPermission(): Promise<boolean> {
  const current = await ImagePicker.getCameraPermissionsAsync();
  if (current.granted) {
    return true;
  }
  const asked = await ImagePicker.requestCameraPermissionsAsync();
  if (asked.granted) {
    return true;
  }
  await explainAndOpenSettings(
    "Camera permission",
    "Allow Camera for Expo Go in system Settings, then return here."
  );
  return false;
}

/**
 * Photos: request when the OS reports a deniable permission.
 * Always offer Settings deep-link when still blocked.
 */
export async function ensurePhotoLibraryPermission(): Promise<boolean> {
  const current = await ImagePicker.getMediaLibraryPermissionsAsync();
  if (current.granted || current.accessPrivileges === "limited") {
    return true;
  }
  // Android photo-picker path often needs no grant; if undecided, ask once.
  if (Platform.OS === "android" && current.status === "undetermined") {
    const asked = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (asked.granted || asked.accessPrivileges === "limited") {
      return true;
    }
    if (asked.canAskAgain !== false && asked.status !== "denied") {
      return true;
    }
  }
  if (Platform.OS === "android" && current.status !== "denied") {
    return true;
  }
  const asked = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (asked.granted || asked.accessPrivileges === "limited") {
    return true;
  }
  if (Platform.OS === "android" && asked.status !== "denied") {
    return true;
  }
  await explainAndOpenSettings(
    "Photos permission",
    Platform.OS === "ios"
      ? "Allow Photos for Expo Go in Settings, then return here."
      : "Allow Photos / Files for Expo Go in Settings if the picker is blocked, then return here."
  );
  return false;
}
