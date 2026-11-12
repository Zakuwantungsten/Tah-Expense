import { useFocusEffect } from "@react-navigation/native";
import type { BottomTabScreenProps } from "@react-navigation/bottom-tabs";
import type { CompositeScreenProps } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ApiError } from "../api/client";
import { downloadReceiptFile } from "../api/receipts";
import { CornerGeometry } from "../components/CornerGeometry";
import type { MainTabParamList, RootStackParamList } from "../navigation";
import { colors, space, type } from "../theme";
import { listUploadHistory, type UploadHistoryEntry } from "../uploadHistory";

type Props = CompositeScreenProps<
  BottomTabScreenProps<MainTabParamList, "History">,
  NativeStackScreenProps<RootStackParamList>
>;

function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return iso;
  }
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

function isImageType(contentType: string, filename: string): boolean {
  if ((contentType || "").toLowerCase().startsWith("image/")) {
    return true;
  }
  return /\.(jpe?g|png|gif|webp|heic)$/i.test(filename || "");
}

export function HistoryScreen({ navigation }: Props) {
  const [rows, setRows] = useState<UploadHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [viewerUri, setViewerUri] = useState<string | null>(null);
  const [viewerTitle, setViewerTitle] = useState("");
  const [opening, setOpening] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await listUploadHistory());
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load])
  );

  const openFile = async (fileId: string, fallbackName: string) => {
    if (opening) {
      return;
    }
    setOpening(true);
    try {
      const file = await downloadReceiptFile(fileId);
      if (isImageType(file.contentType, file.filename)) {
        setViewerTitle(file.filename || fallbackName);
        setViewerUri(file.uri);
      } else {
        Alert.alert(
          "Receipt saved",
          `${file.filename} downloaded to this phone.\n\nOpen it from Files / gallery if needed.`
        );
      }
    } catch (exc) {
      Alert.alert(
        "Could not open",
        exc instanceof ApiError ? exc.message : "Download failed"
      );
    } finally {
      setOpening(false);
    }
  };

  return (
    <SafeAreaView style={styles.root} edges={["top", "left", "right"]}>
      <CornerGeometry corner="topRight" accent={colors.accent} style={styles.geo} />
      <View style={styles.header}>
        <Text style={styles.title}>History</Text>
        <Text style={styles.sub}>Uploads on this phone · not clearable</Text>
      </View>

      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.accent} />
      ) : (
        <FlatList
          data={rows}
          keyExtractor={(item) => item.id}
          contentContainerStyle={rows.length ? styles.list : styles.emptyBox}
          ListEmptyComponent={
            <Text style={styles.empty}>No uploads recorded yet on this phone.</Text>
          }
          renderItem={({ item }) => {
            const requestId =
              item.requestId ||
              (item.requestIds && item.requestIds.length === 1
                ? item.requestIds[0]
                : null);
            const canOpenRequest = !!requestId;
            const fileIds = item.fileIds || [];
            return (
              <View style={styles.card}>
                <Pressable
                  disabled={!canOpenRequest}
                  onPress={() => {
                    if (requestId) {
                      navigation.navigate("Request", { requestId });
                    }
                  }}
                >
                  <Text style={styles.cardTitle}>
                    {item.kind === "early"
                      ? "Parked early"
                      : item.kind === "bulk"
                        ? `Bulk · ${item.requestIds?.length || 0} entries`
                        : item.itemLabel || "Receipt upload"}
                  </Text>
                  <Text style={styles.meta}>{formatWhen(item.at)}</Text>
                  {item.truckNumber ? (
                    <Text style={styles.truck}>Truck {item.truckNumber}</Text>
                  ) : null}
                  <Text style={styles.meta}>
                    Sent {item.sent}
                    {item.failed ? ` · Failed ${item.failed}` : ""}
                    {item.partial ? " · Partial" : ""}
                    {item.status ? ` · ${item.status}` : ""}
                  </Text>
                  {item.names.length ? (
                    <Text style={styles.files} numberOfLines={2}>
                      {item.names.join(", ")}
                    </Text>
                  ) : null}
                </Pressable>

                {fileIds.length ? (
                  <View style={styles.fileActions}>
                    {fileIds.slice(0, 4).map((fid, index) => (
                      <Pressable
                        key={fid}
                        style={styles.viewBtn}
                        disabled={opening}
                        onPress={() =>
                          void openFile(fid, item.names[index] || "Receipt")
                        }
                      >
                        <Text style={styles.viewBtnText}>
                          View {item.names[index] ? item.names[index].slice(0, 12) : `#${index + 1}`}
                        </Text>
                      </Pressable>
                    ))}
                  </View>
                ) : null}

                {canOpenRequest ? (
                  <Text style={styles.openHint}>Tap card to open request</Text>
                ) : null}
              </View>
            );
          }}
        />
      )}

      <Modal
        visible={!!viewerUri}
        transparent
        animationType="fade"
        onRequestClose={() => setViewerUri(null)}
      >
        <View style={styles.modalRoot}>
          <Pressable style={styles.modalBackdrop} onPress={() => setViewerUri(null)} />
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle} numberOfLines={1}>
              {viewerTitle || "Receipt"}
            </Text>
            {viewerUri ? (
              <Image
                source={{ uri: viewerUri }}
                style={styles.preview}
                resizeMode="contain"
              />
            ) : null}
            <Pressable style={styles.closeBtn} onPress={() => setViewerUri(null)}>
              <Text style={styles.closeBtnText}>Close</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  geo: { opacity: 0.85 },
  header: {
    paddingHorizontal: space.lg,
    paddingTop: space.md,
    paddingBottom: space.md,
    backgroundColor: "rgba(255,255,255,0.92)",
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
    zIndex: 1,
  },
  title: { ...type.title, color: colors.text },
  sub: { marginTop: 2, color: colors.muted, ...type.meta },
  list: { padding: space.lg, paddingBottom: 40 },
  emptyBox: { flexGrow: 1, justifyContent: "center", padding: space.xl },
  empty: { textAlign: "center", color: colors.muted, ...type.body },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: space.md,
    marginBottom: space.sm,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardTitle: { ...type.subtitle, color: colors.text },
  meta: { marginTop: 4, ...type.meta, color: colors.muted },
  truck: {
    marginTop: 4,
    ...type.meta,
    color: colors.text,
    fontWeight: "700",
  },
  files: { marginTop: 6, ...type.caption, color: colors.muted },
  fileActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 10,
  },
  viewBtn: {
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 8,
    backgroundColor: "#E8F1FB",
    borderWidth: 1,
    borderColor: colors.border,
  },
  viewBtnText: { ...type.caption, color: colors.accent, fontWeight: "700" },
  openHint: { marginTop: 8, ...type.caption, color: colors.accent, fontWeight: "700" },
  modalRoot: {
    flex: 1,
    justifyContent: "center",
    padding: space.lg,
  },
  modalBackdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(15,23,42,0.72)",
  },
  modalCard: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    padding: space.md,
    zIndex: 1,
    maxHeight: "88%",
  },
  modalTitle: { ...type.subtitle, color: colors.text, marginBottom: 8 },
  preview: { width: "100%", height: 420, backgroundColor: "#0f172a" },
  closeBtn: {
    marginTop: 12,
    alignSelf: "flex-end",
    paddingVertical: 10,
    paddingHorizontal: 16,
    borderRadius: 8,
    backgroundColor: colors.accent,
  },
  closeBtnText: { color: "#fff", fontWeight: "700" },
});
