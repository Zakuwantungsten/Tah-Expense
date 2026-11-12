import type { BottomTabScreenProps } from "@react-navigation/bottom-tabs";
import { useFocusEffect } from "@react-navigation/native";
import type { CompositeScreenProps } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import * as SecureStore from "expo-secure-store";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ApiError } from "../api/client";
import {
  listOpenRequests,
  uploadFilesToManyRequests,
  type ReceiptRequestSummary,
} from "../api/receipts";
import { CornerGeometry } from "../components/CornerGeometry";
import { BellIcon, ProfileIcon } from "../components/HeaderIcons";
import { UploadProgressBar } from "../components/UploadProgressBar";
import type { MainTabParamList, RootStackParamList } from "../navigation";
import { unreadInboxCount } from "../notificationInbox";
import { presentLocalReceiptNotice } from "../notifications";
import { subscribeOpenRequestsLive } from "../openRequestsLive";
import {
  ensureCameraPermission,
  ensurePhotoLibraryPermission,
} from "../permissions";
import { colors, space, type } from "../theme";
import { appendUploadHistory, listUploadHistory, type UploadHistoryEntry } from "../uploadHistory";

type Props = CompositeScreenProps<
  BottomTabScreenProps<MainTabParamList, "Receipts">,
  NativeStackScreenProps<RootStackParamList>
>;

type PickedFile = { uri: string; name: string; mimeType?: string | null };

const OVERDUE_HOURS = 48;
const SEEN_REQUESTS_KEY = "tahmeed.seen_request_ids.v1";

function formatAmount(item: ReceiptRequestSummary): string {
  const amount = item.transaction?.amount;
  const currency = item.transaction?.currency || "TZS";
  if (amount === undefined || amount === null) {
    return currency;
  }
  return `${Number(amount).toLocaleString()} ${currency}`;
}

function formatDate(value?: string): string {
  if (!value) {
    return "";
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    return value.slice(0, 10);
  }
  return d.toLocaleDateString();
}

function ageHours(openedAt?: string): number {
  if (!openedAt) {
    return 0;
  }
  const d = new Date(openedAt);
  if (Number.isNaN(d.getTime())) {
    return 0;
  }
  return Math.max(0, (Date.now() - d.getTime()) / 3600000);
}

function Kpi({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: string;
}) {
  return (
    <View style={styles.kpi}>
      <Text style={[styles.kpiValue, { color: tone }]}>{value}</Text>
      <Text style={styles.kpiLabel}>{label}</Text>
    </View>
  );
}

function formatLastUpload(entry: UploadHistoryEntry | null): string {
  if (!entry) {
    return "No uploads on this phone yet";
  }
  const when = new Date(entry.at);
  const whenLabel = Number.isNaN(when.getTime())
    ? ""
    : when.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
  const kind =
    entry.kind === "early"
      ? "Parked early"
      : entry.kind === "bulk"
        ? "Bulk upload"
        : entry.itemLabel || "Upload";
  const fail = entry.failed ? ` · ${entry.failed} failed` : "";
  return `${whenLabel} · ${kind} · ${entry.sent} sent${fail}`;
}

function guessName(uri: string, fallback: string): string {
  const last = uri.split("/").pop() || fallback;
  if (last.includes(".")) {
    return last.split("?")[0];
  }
  return fallback.endsWith(".jpg") ? fallback : `${fallback}.jpg`;
}

export function PendingListScreen({ navigation }: Props) {
  const [items, setItems] = useState<ReceiptRequestSummary[]>([]);
  const [lastUpload, setLastUpload] = useState<UploadHistoryEntry | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unread, setUnread] = useState(0);
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadDetail, setUploadDetail] = useState("");
  const [partialNote, setPartialNote] = useState("");

  const load = useCallback(async (soft = false) => {
    if (soft) {
      setRefreshing(true);
    } else if (!items.length) {
      setLoading(true);
    }
    setError(null);
    try {
      const [rows, history, unreadN] = await Promise.all([
        listOpenRequests(),
        listUploadHistory(),
        unreadInboxCount(),
      ]);
      setItems(rows);
      setLastUpload(history[0] || null);
      setUnread(unreadN);
      setSelected((prev) => {
        const next = new Set<string>();
        for (const id of prev) {
          if (rows.some((r) => r.id === id)) {
            next.add(id);
          }
        }
        return next;
      });

      const rawSeen = await SecureStore.getItemAsync(SEEN_REQUESTS_KEY);
      const seen = new Set<string>(
        rawSeen ? (JSON.parse(rawSeen) as string[]) : []
      );
      const fresh = rows.filter((r) => r.id && !seen.has(r.id));
      if (seen.size > 0) {
        for (const row of fresh.slice(0, 5)) {
          const item = row.transaction?.item || "Expense";
          const truck = row.transaction?.truck_number
            ? ` · ${row.transaction.truck_number}`
            : "";
          await presentLocalReceiptNotice({
            title: "Receipt needed",
            body: `${item}${truck} · please upload your receipt`,
            requestId: row.id,
            kind: "needed",
          });
        }
        if (fresh.length) {
          setUnread(await unreadInboxCount());
        }
      }
      const nextSeen = Array.from(
        new Set([...Array.from(seen), ...rows.map((r) => r.id)].filter(Boolean))
      ).slice(-200);
      await SecureStore.setItemAsync(SEEN_REQUESTS_KEY, JSON.stringify(nextSeen));
    } catch (exc) {
      // Soft/live refresh must not blank a good list with a transient error.
      if (!soft) {
        setError(
          exc instanceof ApiError ? exc.message : "Could not load requests"
        );
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [items.length]);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load])
  );

  useEffect(() => {
    return subscribeOpenRequestsLive(() => {
      void load(true);
    });
  }, [load]);

  const kpis = useMemo(() => {
    let overdue = 0;
    let reminded = 0;
    for (const row of items) {
      if (ageHours(row.opened_at) >= OVERDUE_HOURS) {
        overdue += 1;
      }
      if ((row.reminder_count || 0) >= 1) {
        reminded += 1;
      }
    }
    return { open: items.length, overdue, reminded };
  }, [items]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const exitSelect = () => {
    setSelectMode(false);
    setSelected(new Set());
    setPartialNote("");
  };

  const pickFiles = async (): Promise<PickedFile[] | null> => {
    return new Promise((resolve) => {
      Alert.alert("Add receipt files", "One receipt can cover many selected entries.", [
        {
          text: "Camera",
          onPress: () => {
            void (async () => {
              if (!(await ensureCameraPermission())) {
                resolve(null);
                return;
              }
              const result = await ImagePicker.launchCameraAsync({
                mediaTypes: ["images"],
                quality: 0.7,
              });
              if (result.canceled || !result.assets?.length) {
                resolve(null);
                return;
              }
              const a = result.assets[0];
              resolve([
                {
                  uri: a.uri,
                  name: a.fileName || guessName(a.uri, `camera-${Date.now()}.jpg`),
                  mimeType: a.mimeType || "image/jpeg",
                },
              ]);
            })();
          },
        },
        {
          text: "Photos",
          onPress: () => {
            void (async () => {
              if (!(await ensurePhotoLibraryPermission())) {
                resolve(null);
                return;
              }
              const result = await ImagePicker.launchImageLibraryAsync({
                mediaTypes: ["images"],
                quality: 0.7,
                allowsMultipleSelection: true,
                selectionLimit: 10,
              });
              if (result.canceled || !result.assets?.length) {
                resolve(null);
                return;
              }
              resolve(
                result.assets.map((a, i) => ({
                  uri: a.uri,
                  name: a.fileName || guessName(a.uri, `photo-${Date.now()}-${i}.jpg`),
                  mimeType: a.mimeType || "image/jpeg",
                }))
              );
            })();
          },
        },
        {
          text: "Files",
          onPress: () => {
            void (async () => {
              const result = await DocumentPicker.getDocumentAsync({
                multiple: true,
                copyToCacheDirectory: true,
                type: ["image/*", "application/pdf"],
              });
              if (result.canceled || !result.assets?.length) {
                resolve(null);
                return;
              }
              resolve(
                result.assets.map((a) => ({
                  uri: a.uri,
                  name: a.name || guessName(a.uri, `file-${Date.now()}.bin`),
                  mimeType: a.mimeType || "application/octet-stream",
                }))
              );
            })();
          },
        },
        { text: "Cancel", style: "cancel", onPress: () => resolve(null) },
      ]);
    });
  };

  const runBulkUpload = async (partial: boolean) => {
    const ids = Array.from(selected);
    if (!ids.length || busy) {
      return;
    }
    if (partial && partialNote.trim().length < 3) {
      Alert.alert("Partial note", "Enter a short note (3+ characters), e.g. covers fuel only.");
      return;
    }
    const files = await pickFiles();
    if (!files?.length) {
      return;
    }
    setBusy(true);
    setUploadProgress(0);
    setUploadDetail(`Uploading to ${ids.length} entr${ids.length === 1 ? "y" : "ies"}…`);
    try {
      const result = await uploadFilesToManyRequests(ids, files, {
        partial,
        partialNote: partial ? partialNote.trim() : undefined,
        onProgress: (ev) => {
          setUploadProgress(ev.overall);
          setUploadDetail(ev.fileName);
        },
      });
      const first = items.find((r) => result.ok.includes(r.id));
      await appendUploadHistory({
        kind: "bulk",
        requestIds: result.ok,
        requestId: result.ok.length === 1 ? result.ok[0] : null,
        itemLabel: first?.transaction?.item || `${result.ok.length} entries`,
        truckNumber: first?.transaction?.truck_number,
        sent: result.ok.length,
        failed: result.failed.length,
        status: partial ? "partial" : "uploaded",
        names: result.sentNames,
        fileIds: result.fileIds,
        errors: result.failed.map((f) => `${f.requestId}: ${f.error}`),
        partial,
      });
      Alert.alert(
        partial ? "Partial upload" : "Bulk upload",
        `Applied to ${result.ok.length} entr${result.ok.length === 1 ? "y" : "ies"}` +
          (result.failed.length ? `\nFailed: ${result.failed.length}` : "")
      );
      exitSelect();
      await load(true);
    } catch (exc) {
      Alert.alert(
        "Upload failed",
        exc instanceof ApiError ? exc.message : "Could not upload"
      );
    } finally {
      setBusy(false);
      setUploadProgress(null);
      setUploadDetail("");
    }
  };

  return (
    <SafeAreaView style={styles.root} edges={["top", "left", "right"]}>
      <CornerGeometry corner="topRight" accent={colors.accent} style={styles.geo} />
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Receipts</Text>
          <Text style={styles.sub}>
            {selectMode
              ? `${selected.size} selected · live updates on`
              : "Open requests · live updates on"}
          </Text>
        </View>
        <View style={styles.headerActions}>
          <Pressable
            style={styles.iconBtn}
            onPress={() => {
              if (selectMode) {
                exitSelect();
              } else {
                setSelectMode(true);
              }
            }}
            hitSlop={8}
          >
            <Text style={styles.selectToggle}>{selectMode ? "Done" : "Select"}</Text>
          </Pressable>
          <Pressable
            style={styles.iconBtn}
            onPress={() => navigation.navigate("Notifications")}
            hitSlop={8}
          >
            <BellIcon color={colors.text} size={22} />
            {unread > 0 ? (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>
                  {unread > 9 ? "9+" : String(unread)}
                </Text>
              </View>
            ) : null}
          </Pressable>
          <Pressable
            style={styles.iconBtn}
            onPress={() => navigation.navigate("Profile")}
            hitSlop={8}
          >
            <ProfileIcon color={colors.text} size={22} />
          </Pressable>
        </View>
      </View>

      <View style={styles.kpiRow}>
        <Kpi label="Open" value={kpis.open} tone={colors.kpiOpen} />
        <Kpi label="Overdue" value={kpis.overdue} tone={colors.kpiOverdue} />
        <Kpi label="Reminded" value={kpis.reminded} tone={colors.kpiReminded} />
      </View>

      <Pressable
        style={styles.lastUpload}
        onPress={() => navigation.navigate("History")}
      >
        <Text style={styles.lastLabel}>Last upload · History</Text>
        <Text style={styles.lastValue} numberOfLines={2}>
          {formatLastUpload(lastUpload)}
        </Text>
      </Pressable>

      {selectMode ? (
        <View style={styles.bulkBar}>
          <Pressable
            onPress={() => setSelected(new Set(items.map((i) => i.id)))}
            hitSlop={6}
          >
            <Text style={styles.bulkLink}>Select all</Text>
          </Pressable>
          <Text style={styles.bulkHint}>1 receipt → many · or partial</Text>
        </View>
      ) : null}

      {selectMode && selected.size > 0 ? (
        <View style={styles.bulkActions}>
          <TextInput
            style={styles.partialInput}
            placeholder="Partial note (optional for full upload)"
            placeholderTextColor={colors.muted}
            value={partialNote}
            onChangeText={setPartialNote}
            editable={!busy}
          />
          <View style={styles.bulkBtnRow}>
            <Pressable
              style={[styles.bulkBtn, styles.bulkPrimary, busy && styles.disabled]}
              disabled={busy}
              onPress={() => void runBulkUpload(false)}
            >
              <Text style={styles.bulkPrimaryText}>Upload to selected</Text>
            </Pressable>
            <Pressable
              style={[styles.bulkBtn, styles.bulkSecondary, busy && styles.disabled]}
              disabled={busy}
              onPress={() => void runBulkUpload(true)}
            >
              <Text style={styles.bulkSecondaryText}>Partial</Text>
            </Pressable>
          </View>
          {uploadProgress != null ? (
            <UploadProgressBar
              progress={uploadProgress}
              label="Bulk uploading"
              detail={uploadDetail}
            />
          ) : null}
        </View>
      ) : null}

      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.accent} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.id}
          contentContainerStyle={items.length ? styles.list : styles.emptyBox}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => void load(true)}
            />
          }
          ListEmptyComponent={
            <Text style={styles.empty}>
              {error || "No open receipt requests. You’re clear."}
            </Text>
          }
          ListHeaderComponent={
            error && items.length ? (
              <Text style={styles.error}>{error}</Text>
            ) : undefined
          }
          renderItem={({ item }) => {
            const overdue = ageHours(item.opened_at) >= OVERDUE_HOURS;
            const isOn = selected.has(item.id);
            const truck = (item.transaction?.truck_number || "").trim();
            return (
              <Pressable
                style={[styles.card, isOn && styles.cardSelected]}
                onPress={() => {
                  if (selectMode) {
                    toggleSelect(item.id);
                    return;
                  }
                  navigation.navigate("Request", { requestId: item.id });
                }}
                onLongPress={() => {
                  setSelectMode(true);
                  toggleSelect(item.id);
                }}
              >
                <View style={styles.cardTop}>
                  {selectMode ? (
                    <View style={[styles.check, isOn && styles.checkOn]}>
                      {isOn ? <Text style={styles.checkMark}>✓</Text> : null}
                    </View>
                  ) : null}
                  <Text style={styles.item}>
                    {item.transaction?.item || "Expense"}
                  </Text>
                  {overdue ? <Text style={styles.overdue}>Overdue</Text> : null}
                  {item.partial_receipt ? (
                    <Text style={styles.partialBadge}>Partial</Text>
                  ) : null}
                </View>
                <Text style={styles.meta}>
                  {formatDate(item.transaction?.date)} · {formatAmount(item)}
                </Text>
                {truck ? <Text style={styles.truck}>Truck {truck}</Text> : null}
                {item.transaction?.description ? (
                  <Text style={styles.description} numberOfLines={2}>
                    {item.transaction.description}
                  </Text>
                ) : null}
                <Text style={styles.payee} numberOfLines={1}>
                  {item.transaction?.payee || "—"}
                </Text>
              </Pressable>
            );
          }}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  geo: { opacity: 0.9 },
  header: {
    paddingTop: space.md,
    paddingHorizontal: space.lg,
    paddingBottom: space.md,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.92)",
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    zIndex: 1,
  },
  title: { ...type.title, color: colors.text },
  sub: { marginTop: 2, color: colors.muted, ...type.meta },
  headerActions: { flexDirection: "row", alignItems: "center", gap: 12 },
  iconBtn: {
    paddingVertical: 4,
    paddingHorizontal: 2,
    alignItems: "center",
    justifyContent: "center",
  },
  selectToggle: {
    ...type.subtitle,
    color: colors.accent,
    fontSize: 13,
    fontWeight: "800",
  },
  badge: {
    position: "absolute",
    top: -2,
    right: -6,
    minWidth: 15,
    height: 15,
    borderRadius: 8,
    backgroundColor: colors.danger,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 3,
  },
  badgeText: { color: "#fff", fontSize: 9, fontWeight: "800" },
  kpiRow: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: space.lg,
    paddingTop: space.md,
    zIndex: 1,
  },
  kpi: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: 12,
    paddingVertical: 10,
    alignItems: "center",
    borderWidth: 1,
    borderColor: colors.border,
  },
  kpiValue: { fontSize: 22, fontWeight: "800" },
  kpiLabel: { marginTop: 2, ...type.caption, color: colors.muted },
  lastUpload: {
    marginTop: space.md,
    marginHorizontal: space.lg,
    padding: space.md,
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    zIndex: 1,
  },
  lastLabel: {
    ...type.caption,
    color: colors.muted,
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  lastValue: { marginTop: 4, ...type.meta, color: colors.text, fontWeight: "600" },
  bulkBar: {
    marginTop: space.sm,
    marginHorizontal: space.lg,
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  bulkLink: { ...type.subtitle, color: colors.accent, fontSize: 13 },
  bulkHint: { ...type.caption, color: colors.muted },
  bulkActions: {
    marginTop: space.sm,
    marginHorizontal: space.lg,
    padding: space.md,
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: colors.border,
    gap: 8,
  },
  partialInput: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    color: colors.text,
    ...type.meta,
  },
  bulkBtnRow: { flexDirection: "row", gap: 8 },
  bulkBtn: {
    flex: 1,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
  },
  bulkPrimary: { backgroundColor: colors.accent },
  bulkPrimaryText: { color: "#fff", fontWeight: "800", fontSize: 13 },
  bulkSecondary: {
    backgroundColor: "#FFF7ED",
    borderWidth: 1,
    borderColor: "#FDBA74",
  },
  bulkSecondaryText: { color: colors.warn, fontWeight: "800", fontSize: 13 },
  disabled: { opacity: 0.5 },
  list: { padding: space.lg, paddingBottom: 28, gap: 10 },
  emptyBox: { flexGrow: 1, justifyContent: "center", padding: space.xl },
  empty: { textAlign: "center", color: colors.muted, ...type.body },
  error: { color: colors.danger, marginBottom: 10 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: space.md,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: 10,
  },
  cardSelected: {
    borderColor: colors.accent,
    backgroundColor: "#F0F7FC",
  },
  cardTop: { flexDirection: "row", alignItems: "center", gap: 8 },
  check: {
    width: 22,
    height: 22,
    borderRadius: 6,
    borderWidth: 2,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  checkOn: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  checkMark: { color: "#fff", fontSize: 12, fontWeight: "900" },
  item: { ...type.subtitle, fontSize: 16, color: colors.text, flex: 1 },
  overdue: {
    color: colors.danger,
    fontSize: 11,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  partialBadge: {
    color: colors.warn,
    fontSize: 11,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  meta: { marginTop: 4, color: colors.muted, ...type.meta },
  truck: {
    marginTop: 4,
    color: colors.text,
    ...type.meta,
    fontWeight: "700",
  },
  description: {
    marginTop: 4,
    color: colors.text,
    ...type.meta,
  },
  payee: { marginTop: 4, color: colors.muted, ...type.meta },
});
