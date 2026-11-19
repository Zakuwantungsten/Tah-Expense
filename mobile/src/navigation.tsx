import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import * as Notifications from "expo-notifications";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { fetchStorageHealth, isStorageDegraded, type StorageHealth } from "./api/storageHealth";
import { useAuth } from "./auth/AuthContext";
import { ProfileIcon } from "./components/HeaderIcons";
import { unreadInboxCount } from "./notificationInbox";
import {
  captureNotificationToInbox,
  registerForPushNotifications,
  requestIdFromNotification,
} from "./notifications";
import { notifyOpenRequestsChanged } from "./openRequestsLive";
import { EarlyUploadScreen } from "./screens/EarlyUploadScreen";
import { HistoryScreen } from "./screens/HistoryScreen";
import { LoginScreen } from "./screens/LoginScreen";
import { NotificationsScreen } from "./screens/NotificationsScreen";
import { PendingListScreen } from "./screens/PendingListScreen";
import { ProfileScreen } from "./screens/ProfileScreen";
import { RequestDetailScreen } from "./screens/RequestDetailScreen";
import { StorageDownScreen } from "./screens/StorageDownScreen";
import { colors } from "./theme";

export type RootStackParamList = {
  Login: undefined;
  Main: undefined;
  Request: { requestId: string };
  Notifications: undefined;
};

export type MainTabParamList = {
  Receipts: undefined;
  Upload: undefined;
  History: undefined;
  Profile: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();
const Tab = createBottomTabNavigator<MainTabParamList>();

function TabIcon({ label, focused }: { label: string; focused: boolean }) {
  const tint = focused ? colors.accent : colors.muted;
  if (label === "Profile") {
    return (
      <View style={tabStyles.iconWrap}>
        <ProfileIcon color={tint} size={20} />
      </View>
    );
  }
  if (label === "Upload") {
    return (
      <View style={tabStyles.iconWrap}>
        <Text style={[tabStyles.icon, { color: tint }]}>↑</Text>
      </View>
    );
  }
  if (label === "History") {
    return (
      <View style={tabStyles.iconWrap}>
        <Text style={[tabStyles.icon, { color: tint }]}>☰</Text>
      </View>
    );
  }
  return (
    <View style={tabStyles.iconWrap}>
      <Text style={[tabStyles.icon, { color: tint }]}>▣</Text>
    </View>
  );
}

function MainTabs() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarActiveTintColor: colors.accent,
        tabBarInactiveTintColor: colors.muted,
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          height: 58,
          paddingBottom: 6,
          paddingTop: 4,
        },
        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: "700",
        },
        tabBarIcon: ({ focused }) => (
          <TabIcon label={route.name} focused={focused} />
        ),
      })}
    >
      <Tab.Screen name="Receipts" component={PendingListScreen} />
      <Tab.Screen
        name="Upload"
        component={EarlyUploadScreen}
        options={{ title: "Upload" }}
      />
      <Tab.Screen name="History" component={HistoryScreen} />
      <Tab.Screen name="Profile" component={ProfileScreen} />
    </Tab.Navigator>
  );
}

function useStorageHealth(enabled: boolean) {
  const [health, setHealth] = useState<StorageHealth | null>(null);
  const [checking, setChecking] = useState(true);

  const refreshHealth = useCallback(async () => {
    if (!enabled) {
      setChecking(false);
      return;
    }
    try {
      const next = await fetchStorageHealth();
      setHealth(next);
    } catch {
      // Keep last known state; network blips should not force WhatsApp mode.
    } finally {
      setChecking(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) {
      setHealth(null);
      setChecking(false);
      return;
    }
    setChecking(true);
    void refreshHealth();
    const id = setInterval(() => {
      void refreshHealth();
    }, 30_000);
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        void refreshHealth();
      }
    });
    return () => {
      clearInterval(id);
      sub.remove();
    };
  }, [enabled, refreshHealth]);

  return { health, checking, refreshHealth };
}

export function RootNavigator() {
  const { ready, signedIn } = useAuth();
  const navigationRef = useRef<any>(null);
  const pendingRequestId = useRef<string | null>(null);
  const [, setUnreadTick] = useState(0);
  const { health, checking, refreshHealth } = useStorageHealth(signedIn);
  const storageDown = signedIn && isStorageDegraded(health);

  useEffect(() => {
    if (!signedIn) {
      return;
    }
    void registerForPushNotifications();
  }, [signedIn]);

  useEffect(() => {
    const received = Notifications.addNotificationReceivedListener((notification) => {
      void captureNotificationToInbox(notification).then(() => {
        setUnreadTick((n) => n + 1);
        notifyOpenRequestsChanged();
      });
    });
    const response = Notifications.addNotificationResponseReceivedListener(
      (resp) => {
        void captureNotificationToInbox(resp.notification).then(() => {
          setUnreadTick((n) => n + 1);
          notifyOpenRequestsChanged();
        });
        const requestId = requestIdFromNotification(resp);
        if (!requestId) {
          return;
        }
        if (navigationRef.current?.isReady?.()) {
          navigationRef.current.navigate("Request", { requestId });
        } else {
          pendingRequestId.current = requestId;
        }
      }
    );
    void Notifications.getLastNotificationResponseAsync().then((resp) => {
      const requestId = requestIdFromNotification(resp);
      if (requestId) {
        pendingRequestId.current = requestId;
      }
    });
    return () => {
      received.remove();
      response.remove();
    };
  }, []);

  useEffect(() => {
    if (!signedIn) {
      return;
    }
    const id = setInterval(() => {
      void unreadInboxCount().then(() => setUnreadTick((n) => n + 1));
    }, 15000);
    return () => clearInterval(id);
  }, [signedIn]);

  if (!ready || (signedIn && checking && !health)) {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.bg }}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  if (storageDown) {
    return (
      <StorageDownScreen
        message={health?.message}
        hint={health?.whatsapp_hint}
        onRetry={() => {
          void refreshHealth();
        }}
      />
    );
  }

  return (
    <NavigationContainer
      ref={navigationRef}
      onReady={() => {
        if (signedIn && pendingRequestId.current) {
          navigationRef.current?.navigate("Request", {
            requestId: pendingRequestId.current,
          });
          pendingRequestId.current = null;
        }
      }}
    >
      <Stack.Navigator>
        {!signedIn ? (
          <Stack.Screen
            name="Login"
            component={LoginScreen}
            options={{ headerShown: false }}
          />
        ) : (
          <>
            <Stack.Screen
              name="Main"
              component={MainTabs}
              options={{ headerShown: false }}
            />
            <Stack.Screen
              name="Request"
              component={RequestDetailScreen}
              options={{ title: "Receipt request" }}
            />
            <Stack.Screen
              name="Notifications"
              component={NotificationsScreen}
              options={{ title: "Notifications", headerShown: false }}
            />
          </>
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const tabStyles = StyleSheet.create({
  iconWrap: { alignItems: "center", justifyContent: "center" },
  icon: { fontSize: 16, color: colors.muted, fontWeight: "700" },
  iconOn: { color: colors.accent },
});
