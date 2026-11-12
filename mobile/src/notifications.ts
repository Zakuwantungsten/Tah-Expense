import Constants from "expo-constants";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { registerDevice } from "./api/auth";
import { appendInboxNotification } from "./notificationInbox";

const RECEIPTS_CHANNEL = "receipts";

/** Show on lock screen / tray even while the app is open. */
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

function isExpoGo(): boolean {
  return Constants.appOwnership === "expo";
}

async function ensureAndroidChannel(): Promise<void> {
  if (Platform.OS !== "android") {
    return;
  }
  await Notifications.setNotificationChannelAsync(RECEIPTS_CHANNEL, {
    name: "Receipt requests",
    description: "When a receipt is needed or uploaded for you",
    importance: Notifications.AndroidImportance.MAX,
    vibrationPattern: [0, 250, 120, 250],
    lightColor: "#0077C5",
    lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
    bypassDnd: false,
    enableVibrate: true,
    showBadge: true,
  });
}

export async function registerForPushNotifications(): Promise<string | null> {
  // Expo Go (Android SDK 53+) cannot register remote push tokens; skip so login
  // is not blocked. Use a development build for real device push + wake.
  if (!Device.isDevice) {
    return null;
  }
  try {
    await ensureAndroidChannel();
    const existing = await Notifications.getPermissionsAsync();
    let status = existing.status;
    if (status !== "granted") {
      const asked = await Notifications.requestPermissionsAsync({
        ios: {
          allowAlert: true,
          allowBadge: true,
          allowSound: true,
          allowDisplayInCarPlay: false,
        },
      });
      status = asked.status;
    }
    if (status !== "granted") {
      return null;
    }
    if (isExpoGo()) {
      return null;
    }
    const projectId =
      Constants.easConfig?.projectId ||
      (Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined)
        ?.eas?.projectId;
    const token = projectId
      ? await Notifications.getExpoPushTokenAsync({ projectId })
      : await Notifications.getExpoPushTokenAsync();
    const value = token.data;
    if (value) {
      try {
        await registerDevice(value);
      } catch {
        // Token registration is best-effort; uploads still work.
      }
    }
    return value;
  } catch {
    return null;
  }
}

export function requestIdFromNotification(
  response: Notifications.NotificationResponse | null
): string | null {
  if (!response) {
    return null;
  }
  const data = response.notification.request.content.data as {
    request_id?: string;
  };
  return data?.request_id || null;
}

export function requestIdFromContent(
  notification: Notifications.Notification | null
): string | null {
  if (!notification) {
    return null;
  }
  const data = notification.request.content.data as { request_id?: string };
  return data?.request_id || null;
}

/** Persist into the in-app panel whenever a push arrives (fg or bg tap). */
export async function captureNotificationToInbox(
  notification: Notifications.Notification
): Promise<void> {
  const content = notification.request.content;
  const data = (content.data || {}) as {
    request_id?: string;
    kind?: string;
  };
  await appendInboxNotification({
    id: notification.request.identifier,
    title: content.title || "Receipt update",
    body: content.body || "",
    requestId: data.request_id || null,
    kind: data.kind || null,
  });
}

/**
 * Present a local system notification (tray + sound). Useful when sync finds
 * new open requests while the app is foregrounded, and as a fallback path.
 */
export async function presentLocalReceiptNotice(opts: {
  title: string;
  body: string;
  requestId?: string;
  kind?: string;
}): Promise<void> {
  await ensureAndroidChannel();
  const id = await Notifications.scheduleNotificationAsync({
    content: {
      title: opts.title,
      body: opts.body,
      sound: "default",
      ...(Platform.OS === "android" ? { channelId: RECEIPTS_CHANNEL } : {}),
      data: {
        request_id: opts.requestId || "",
        kind: opts.kind || "needed",
      },
      ...(Platform.OS === "android"
        ? {
            priority: Notifications.AndroidNotificationPriority.MAX,
          }
        : {}),
    },
    trigger: null,
  });
  await appendInboxNotification({
    id,
    title: opts.title,
    body: opts.body,
    requestId: opts.requestId || null,
    kind: opts.kind || "needed",
  });
}
