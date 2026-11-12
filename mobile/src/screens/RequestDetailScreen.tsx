import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { ApiError } from "../api/client";
import {
  formatUploadSummary,
  getRequest,
  uploadFiles,
  waiveRequest,
  type ReceiptRequestSummary,
} from "../api/receipts";
import type { RootStackParamList } from "../navigation";
import {
  ensureCameraPermission,
  ensurePhotoLibraryPermission,
} from "../permissions";
import { UploadProgressBar } from "../components/UploadProgressBar";
import { colors, space, type } from "../theme";
import { appendUploadHistory } from "../uploadHistory";

type Props = NativeStackScreenProps<RootStackParamList, "Request">;

type PickedFile = {
  uri: string;
  name: string;
  mimeType?: string | null;
};

function guessName(uri: string, fallback: string): string {
  const last = uri.split("/").pop() || fallback;
  if (last.includes(".")) {
    return last.split("?")[0];
  }
  return fallback.endsWith(".jpg") ? fallback : `${fallback}.jpg`;
}

function assetToPicked(
  asset: ImagePicker.ImagePickerAsset,
  fallback: string
): PickedFile {
  return {
    uri: asset.uri,
    name: asset.fileName || guessName(asset.uri, fallback),
    mimeType: asset.mimeType || "image/jpeg",
  };
}

export function RequestDetailScreen({ navigation, route }: Props) {
  const { requestId } = route.params;
  const [item, setItem] = useState<ReceiptRequestSummary | null>(null);
  const [picked, setPicked] = useState<PickedFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [waiverReason, setWaiverReason] = useState("");
  const [partialNote, setPartialNote] = useState("");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadDetail, setUploadDetail] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItem(await getRequest(requestId));
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "Could not load request");
    } finally {
      setLoading(false);
    }
  }, [requestId]);

  useEffect(() => {
    void load();
  }, [load]);

  const addCamera = async () => {
    try {
      if (!(await ensureCameraPermission())) {
        return;
      }
      const result = await ImagePicker.launchCameraAsync({
        mediaTypes: ["images"],
        quality: 0.7,
        allowsEditing: false,
        exif: false,
      });
      if (result.canceled || !result.assets?.length) {
        return;
      }
      setPicked((prev) => [
        ...prev,
        assetToPicked(result.assets[0], `camera-${Date.now()}.jpg`),
      ]);
    } catch (exc) {
      Alert.alert(
        "Camera failed",
        exc instanceof Error ? exc.message : "Could not open the camera."
      );
    }
  };

  const addLibrary = async () => {
    try {
      if (!(await ensurePhotoLibraryPermission())) {
        return;
      }
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ["images"],
        quality: 0.7,
        allowsMultipleSelection: true,
        selectionLimit: 10,
        exif: false,
      });
      if (result.canceled || !result.assets?.length) {
        return;
      }
      setPicked((prev) => [
        ...prev,
        ...result.assets.map((asset, index) =>
          assetToPicked(asset, `photo-${Date.now()}-${index}.jpg`)
        ),
      ]);
    } catch (exc) {
      Alert.alert(
        "Photos failed",
        exc instanceof Error ? exc.message : "Could not open the photo picker."
      );
    }
  };

  const addDocuments = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        multiple: true,
        copyToCacheDirectory: true,
        type: ["image/*", "application/pdf"],
      });
      if (result.canceled || !result.assets?.length) {
        return;
      }
      setPicked((prev) => [
        ...prev,
        ...result.assets.map((asset) => ({
          uri: asset.uri,
          name: asset.name || guessName(asset.uri, `file-${Date.now()}.bin`),
          mimeType: asset.mimeType || "application/octet-stream",
        })),
      ]);
    } catch (exc) {
      Alert.alert(
        "Files failed",
        exc instanceof Error ? exc.message : "Could not open the file picker."
      );
    }
  };

  const onUpload = async (partial = false) => {
    if (!picked.length || busy) {
      return;
    }
    if (partial && partialNote.trim().length < 3) {
      Alert.alert(
        "Partial note",
        "Enter a short note (3+ characters), e.g. covers fuel only."
      );
      return;
    }
    setBusy(true);
    setError(null);
    setUploadProgress(0);
    setUploadDetail(`Preparing ${picked.length} file(s)…`);
    try {
      const summary = await uploadFiles(requestId, picked, {
        partial,
        partialNote: partial ? partialNote.trim() : undefined,
        onProgress: (ev) => {
          setUploadProgress(ev.overall);
          setUploadDetail(
            `File ${ev.fileIndex + 1} of ${ev.fileCount}: ${ev.fileName}`
          );
        },
      });
      if (summary.request) {
        setItem(summary.request);
      } else {
        await load();
      }
      await appendUploadHistory({
        kind: "request",
        requestId,
        itemLabel: item?.transaction?.item || summary.request?.transaction?.item,
        truckNumber:
          item?.transaction?.truck_number ||
          summary.request?.transaction?.truck_number,
        sent: summary.sent.length,
        failed: summary.failed.length,
        status: summary.request?.status,
        names: summary.sent.map((s) => s.name),
        fileIds: summary.sent.map((s) => s.id).filter(Boolean) as string[],
        errors: summary.failed.map((f) => `${f.name}: ${f.error}`),
        partial,
      });
      const title =
        summary.failed.length === 0
          ? partial
            ? "Partial upload"
            : "Uploaded"
          : summary.sent.length
            ? "Partially uploaded"
            : "Upload failed";
      setPicked([]);
      Alert.alert(title, formatUploadSummary(summary), [
        {
          text: "OK",
          onPress: () => {
            if (summary.request?.status === "received") {
              navigation.navigate("Main");
            }
          },
        },
      ]);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "Upload failed");
    } finally {
      setBusy(false);
      setUploadProgress(null);
      setUploadDetail("");
    }
  };

  const onWaive = async () => {
    const reason = waiverReason.trim();
    if (!reason) {
      Alert.alert("Reason required", "Say why there is no receipt.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await waiveRequest(requestId, reason);
      setItem(updated);
      Alert.alert("Waived", "Marked as no receipt.", [
        { text: "OK", onPress: () => navigation.navigate("Main") },
      ]);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "Could not waive");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  if (!item) {
    return (
      <View style={styles.center}>
        <Text style={styles.error}>{error || "Request not found"}</Text>
      </View>
    );
  }

  const tx = item.transaction || {};
  const done = item.status === "received" || item.status === "waived";

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.heading}>{tx.item || "Expense"}</Text>
      <Text style={styles.line}>
        {tx.amount != null ? Number(tx.amount).toLocaleString() : "—"} {tx.currency || ""}
      </Text>
      <Text style={styles.muted}>Payee: {tx.payee || "—"}</Text>
      {tx.truck_number ? (
        <Text style={styles.muted}>Truck: {tx.truck_number}</Text>
      ) : null}
      <Text style={styles.muted}>Ownership: {tx.ownership || item.asked_of_name || "—"}</Text>
      <Text style={styles.muted}>Status: {item.status}</Text>
      {item.partial_receipt ? (
        <Text style={styles.muted}>
          Partial{item.partial_note ? `: ${item.partial_note}` : ""}
        </Text>
      ) : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      {!done ? (
        <>
          <Text style={styles.section}>Add files</Text>
          <View style={styles.row}>
            <Pressable style={styles.secondary} onPress={() => void addCamera()} disabled={busy}>
              <Text style={styles.secondaryText}>Camera</Text>
            </Pressable>
            <Pressable style={styles.secondary} onPress={() => void addLibrary()} disabled={busy}>
              <Text style={styles.secondaryText}>Photos</Text>
            </Pressable>
            <Pressable style={styles.secondary} onPress={() => void addDocuments()} disabled={busy}>
              <Text style={styles.secondaryText}>Files</Text>
            </Pressable>
          </View>

          {picked.map((file) => (
            <View key={`${file.uri}-${file.name}`} style={styles.fileRow}>
              <Text style={styles.file} numberOfLines={1}>
                {file.name}
              </Text>
              <Pressable
                onPress={() =>
                  setPicked((prev) => prev.filter((p) => p.uri !== file.uri || p.name !== file.name))
                }
                disabled={busy}
              >
                <Text style={styles.remove}>Remove</Text>
              </Pressable>
            </View>
          ))}

          {uploadProgress != null ? (
            <UploadProgressBar
              progress={uploadProgress}
              label="Uploading receipts"
              detail={uploadDetail}
            />
          ) : null}

          <TextInput
            style={styles.input}
            placeholder="Partial note (only for Partial upload)"
            value={partialNote}
            onChangeText={setPartialNote}
            editable={!busy}
          />
          <Pressable
            style={[styles.primary, (!picked.length || busy) && styles.disabled]}
            onPress={() => void onUpload(false)}
            disabled={!picked.length || busy}
          >
            {busy && uploadProgress == null ? (
              <ActivityIndicator color="#FFFFFF" />
            ) : (
              <Text style={styles.primaryText}>
                {busy
                  ? "Uploading…"
                  : `Upload full ${picked.length ? `(${picked.length})` : ""}`}
              </Text>
            )}
          </Pressable>
          <Pressable
            style={[styles.secondaryWide, (!picked.length || busy) && styles.disabled]}
            onPress={() => void onUpload(true)}
            disabled={!picked.length || busy}
          >
            <Text style={styles.secondaryWideText}>Upload partial (keeps request open)</Text>
          </Pressable>
          <Text style={styles.caption}>
            Full closes the request. Partial attaches files but leaves it open for more.
            Many files on one entry, or use Select on Receipts for one receipt → many entries.
          </Text>

          <Text style={styles.section}>No receipt</Text>
          <TextInput
            style={styles.input}
            placeholder="Reason (required)"
            value={waiverReason}
            onChangeText={setWaiverReason}
            editable={!busy}
            multiline
          />
          <Pressable
            style={[styles.danger, busy && styles.disabled]}
            onPress={() => void onWaive()}
            disabled={busy}
          >
            <Text style={styles.primaryText}>Waive</Text>
          </Pressable>
        </>
      ) : (
        <Text style={styles.done}>
          {item.status === "waived"
            ? `Waived: ${item.waiver_reason || ""}`
            : "Receipt already received."}
        </Text>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  content: { padding: space.lg, paddingBottom: 40 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.bg },
  heading: { ...type.title, fontSize: 22, color: colors.text },
  line: { marginTop: 6, fontSize: 18, color: colors.text, fontWeight: "600" },
  muted: { marginTop: 4, color: colors.muted, ...type.meta },
  section: {
    marginTop: 22,
    marginBottom: 8,
    ...type.subtitle,
    color: colors.text,
  },
  row: { flexDirection: "row", gap: space.sm },
  secondaryWide: {
    marginTop: 8,
    borderWidth: 1,
    borderColor: "#FDBA74",
    backgroundColor: "#FFF7ED",
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
  },
  secondaryWideText: { color: colors.warn, fontWeight: "800", fontSize: 13 },
  secondary: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: "center",
  },
  secondaryText: { color: colors.text, fontWeight: "600" },
  fileRow: {
    marginTop: space.sm,
    flexDirection: "row",
    alignItems: "center",
    gap: space.sm,
    backgroundColor: colors.surface,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: space.md,
    paddingVertical: 10,
  },
  file: { flex: 1, color: colors.text, fontSize: 13 },
  remove: { color: colors.danger, fontWeight: "600", fontSize: 12 },
  primary: {
    marginTop: space.lg,
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: "center",
  },
  danger: {
    marginTop: 10,
    backgroundColor: colors.warn,
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: "center",
  },
  primaryText: { color: "#FFFFFF", ...type.button },
  disabled: { opacity: 0.6 },
  caption: { marginTop: 8, color: colors.muted, ...type.caption },
  input: {
    minHeight: 72,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    padding: 10,
    backgroundColor: colors.surface,
    textAlignVertical: "top",
  },
  error: { marginTop: 10, color: colors.danger },
  done: { marginTop: 24, color: colors.ok, fontSize: 15 },
});
