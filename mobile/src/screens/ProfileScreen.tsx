import type { BottomTabScreenProps } from "@react-navigation/bottom-tabs";
import type { CompositeScreenProps } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useAuth } from "../auth/AuthContext";
import { loadRemembered } from "../auth/remember";
import { API_BASE_URL } from "../config";
import type { MainTabParamList, RootStackParamList } from "../navigation";
import { colors, space, type } from "../theme";

type Props = CompositeScreenProps<
  BottomTabScreenProps<MainTabParamList, "Profile">,
  NativeStackScreenProps<RootStackParamList>
>;

export function ProfileScreen({ navigation }: Props) {
  const { username, signOut } = useAuth();
  const [displayName, setDisplayName] = useState(username || "");

  useEffect(() => {
    let alive = true;
    (async () => {
      if (username) {
        setDisplayName(username);
        return;
      }
      const saved = await loadRemembered();
      if (alive && saved.username) {
        setDisplayName(saved.username);
      }
    })();
    return () => {
      alive = false;
    };
  }, [username]);

  const initial = (displayName || "?").trim().charAt(0).toUpperCase() || "?";

  return (
    <SafeAreaView style={styles.root} edges={["top", "left", "right"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Profile</Text>
        <Text style={styles.sub}>Account on this phone</Text>
      </View>

      <View style={styles.card}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{initial}</Text>
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.name}>{displayName || "Signed in"}</Text>
          <Text style={styles.meta}>Receipt capture</Text>
        </View>
      </View>

      <Pressable
        style={styles.row}
        onPress={() => navigation.navigate("Notifications")}
      >
        <Text style={styles.rowTitle}>Notifications</Text>
        <Text style={styles.rowChevron}>›</Text>
      </Pressable>

      <Pressable
        style={styles.row}
        onPress={() => navigation.navigate("History")}
      >
        <Text style={styles.rowTitle}>History</Text>
        <Text style={styles.rowMeta}>Uploads · not clearable</Text>
        <Text style={styles.rowChevron}>›</Text>
      </Pressable>

      <Pressable
        style={[styles.row, styles.dangerRow]}
        onPress={() => void signOut()}
      >
        <Text style={styles.dangerText}>Sign out</Text>
      </Pressable>

      <Text style={styles.api} selectable>
        {API_BASE_URL}
      </Text>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  header: {
    paddingHorizontal: space.lg,
    paddingTop: space.lg,
    paddingBottom: space.md,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  title: { ...type.title, color: colors.text },
  sub: { marginTop: 2, color: colors.muted, ...type.meta },
  card: {
    marginTop: space.lg,
    marginHorizontal: space.lg,
    padding: space.lg,
    borderRadius: 14,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
  },
  avatar: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarText: { color: "#FFFFFF", fontSize: 22, fontWeight: "800" },
  name: { ...type.subtitle, fontSize: 17, color: colors.text },
  meta: { marginTop: 2, ...type.meta, color: colors.muted },
  row: {
    marginTop: space.md,
    marginHorizontal: space.lg,
    paddingVertical: 16,
    paddingHorizontal: space.lg,
    borderRadius: 14,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  rowTitle: { ...type.subtitle, color: colors.text, fontSize: 15, flex: 1 },
  rowMeta: { ...type.caption, color: colors.muted, marginRight: 8 },
  rowChevron: { color: colors.muted, fontSize: 22, fontWeight: "300" },
  dangerRow: { marginTop: space.sm },
  dangerText: { ...type.subtitle, color: colors.danger, fontSize: 15 },
  api: {
    marginTop: space.xl,
    marginHorizontal: space.lg,
    textAlign: "center",
    color: colors.muted,
    fontSize: 11,
  },
});
