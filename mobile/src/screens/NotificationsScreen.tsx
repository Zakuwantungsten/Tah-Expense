import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useFocusEffect } from "@react-navigation/native";
import React, { useCallback, useState } from "react";
import {
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import type { RootStackParamList } from "../navigation";
import {
  clearInbox,
  listInboxNotifications,
  markInboxRead,
  type InboxNotification,
} from "../notificationInbox";
import { colors, space, type } from "../theme";

type Props = NativeStackScreenProps<RootStackParamList, "Notifications">;

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return "";
  }
  return d.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function NotificationsScreen({ navigation }: Props) {
  const [items, setItems] = useState<InboxNotification[]>([]);

  const load = useCallback(async () => {
    const rows = await listInboxNotifications();
    setItems(rows);
    await markInboxRead();
  }, []);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load])
  );

  return (
    <SafeAreaView style={styles.root} edges={["top", "left", "right"]}>
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Notifications</Text>
          <Text style={styles.sub}>Receipt requests & updates on this phone</Text>
        </View>
        {items.length ? (
          <Pressable
            onPress={() => {
              void clearInbox().then(() => setItems([]));
            }}
            hitSlop={8}
          >
            <Text style={styles.clear}>Clear</Text>
          </Pressable>
        ) : null}
      </View>

      <FlatList
        data={items}
        keyExtractor={(item) => item.id}
        contentContainerStyle={items.length ? styles.list : styles.emptyBox}
        ListEmptyComponent={
          <Text style={styles.empty}>
            No notifications yet. When a receipt is requested (or uploaded for
            you), it shows here and on your phone’s notification shade.
          </Text>
        }
        renderItem={({ item }) => (
          <Pressable
            style={[styles.card, !item.read && styles.cardUnread]}
            onPress={() => {
              if (item.requestId) {
                navigation.navigate("Request", { requestId: item.requestId });
              }
            }}
          >
            <View style={styles.cardTop}>
              <Text style={styles.cardTitle} numberOfLines={1}>
                {item.title}
              </Text>
              {!item.read ? <View style={styles.dot} /> : null}
            </View>
            {item.body ? (
              <Text style={styles.cardBody} numberOfLines={3}>
                {item.body}
              </Text>
            ) : null}
            <Text style={styles.cardMeta}>{formatWhen(item.at)}</Text>
          </Pressable>
        )}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  header: {
    paddingHorizontal: space.lg,
    paddingTop: space.md,
    paddingBottom: space.md,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  title: { ...type.title, color: colors.text },
  sub: { marginTop: 2, color: colors.muted, ...type.meta },
  clear: { color: colors.danger, fontWeight: "700", fontSize: 13 },
  list: { padding: space.lg, paddingBottom: 28 },
  emptyBox: { flexGrow: 1, justifyContent: "center", padding: space.xl },
  empty: { textAlign: "center", color: colors.muted, ...type.body, lineHeight: 22 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: space.md,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: 10,
  },
  cardUnread: { borderColor: "#BFDBFE", backgroundColor: "#F8FBFF" },
  cardTop: { flexDirection: "row", alignItems: "center", gap: 8 },
  cardTitle: { ...type.subtitle, color: colors.text, flex: 1, fontSize: 15 },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: colors.accent,
  },
  cardBody: { marginTop: 6, color: colors.text, ...type.meta, lineHeight: 18 },
  cardMeta: { marginTop: 8, color: colors.muted, ...type.caption },
});
