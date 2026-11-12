import type { BottomTabScreenProps } from "@react-navigation/bottom-tabs";
import type { CompositeScreenProps } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import React, { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { ApiError } from "../api/client";
import { formatUploadSummary, uploadEarlyFiles } from "../api/receipts";
import { UploadProgressBar } from "../components/UploadProgressBar";
import type { MainTabParamList, RootStackParamList } from "../navigation";
import {
  ensureCameraPermission,
  ensurePhotoLibraryPermission,
} from "../permissions";
import { colors, space, type } from "../theme";
import { appendUploadHistory } from "../uploadHistory";

type Props = CompositeScreenProps<
  BottomTabScreenProps<MainTabParamList, "Upload">,
  NativeStackScreenProps<RootStackParamList>
>;

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

export function EarlyUploadScreen({ navigation }: Props) {
  const [picked, setPicked] = useState<PickedFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadDetail, setUploadDetail] = useState("");

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
      const asset = result.assets[0];
      setPicked((prev) => [
        ...prev,
        {
          uri: asset.uri,
          name: asset.fileName || guessName(asset.uri, `early-${Date.now()}.jpg`),
          mimeType: asset.mimeType || "image/jpeg",
        },
      ]);
    } catch (exc) {
      Alert.alert("Camera failed", exc instanceof Error ? exc.message : String(exc));
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
        selectionLimit: 8,
      });
      if (result.canceled || !result.assets?.length) {
        return;
      }
      setPicked((prev) => [
        ...prev,
        ...result.assets.map((asset, i) => ({
          uri: asset.uri,
          name: asset.fileName || guessName(asset.uri, `photo-${Date.now()}-${i}.jpg`),
          mimeType: asset.mimeType || "image/jpeg",
        })),
      ]);
    } catch (exc) {
      Alert.alert("Photos failed", exc instanceof Error ? exc.message : String(exc));
    }
  };

  const addFiles = async () => {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        multiple: true,
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets?.length) {
        return;
      }
      setPicked((prev) => [
        ...prev,
        ...result.assets.map((asset) => ({
          uri: asset.uri,
          name: asset.name || guessName(asset.uri, "file.pdf"),
          mimeType: asset.mimeType,
        })),
      ]);
    } catch (exc) {
      Alert.alert("Files failed", exc instanceof Error ? exc.message : String(exc));
    }
  };

  const submit = async () => {
    if (!picked.length) {
      Alert.alert("Nothing selected", "Take a photo or pick files first.");
      return;
    }
    setBusy(true);
    setUploadProgress(0);
    setUploadDetail(`Preparing ${picked.length} file(s)…`);
    try {
      const summary = await uploadEarlyFiles(picked, (ev) => {
        setUploadProgress(ev.overall);
        setUploadDetail(
          `File ${ev.fileIndex + 1} of ${ev.fileCount}: ${ev.fileName}`
        );
      });
      await appendUploadHistory({
        kind: "early",
        itemLabel: "Parked early",
        sent: summary.sent.length,
        failed: summary.failed.length,
        status: "parked",
        names: summary.sent.map((s) => s.name),
        fileIds: summary.sent.map((s) => s.id).filter(Boolean) as string[],
        errors: summary.failed.map((f) => `${f.name}: ${f.error}`),
      });
      const title =
        summary.failed.length === 0
          ? "Parked"
          : summary.sent.length
            ? "Partially parked"
            : "Park failed";
      setPicked([]);
      Alert.alert(
        title,
        `${formatUploadSummary(summary)}\n\nDesk can link parked files from Inbox.`,
        [{ text: "OK", onPress: () => navigation.navigate("Receipts") }]
      );
    } catch (exc) {
      Alert.alert(
        "Upload failed",
        exc instanceof ApiError ? exc.message : "Could not park files"
      );
    } finally {
      setBusy(false);
      setUploadProgress(null);
      setUploadDetail("");
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top", "left", "right"]}>
      <ScrollView contentContainerStyle={styles.root} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Upload</Text>
        <Text style={styles.lead}>
          No matching request yet? Send the photo now. It lands in the desk parked
          inbox — nothing is lost while the row is created.
        </Text>
        <View style={styles.row}>
          <Pressable style={styles.btn} onPress={() => void addCamera()} disabled={busy}>
            <Text style={styles.btnText}>Camera</Text>
          </Pressable>
          <Pressable style={styles.btn} onPress={() => void addLibrary()} disabled={busy}>
            <Text style={styles.btnText}>Photos</Text>
          </Pressable>
          <Pressable style={styles.btn} onPress={() => void addFiles()} disabled={busy}>
            <Text style={styles.btnText}>Files</Text>
          </Pressable>
        </View>
        {picked.map((file, index) => (
          <View key={`${file.uri}-${index}`} style={styles.fileRow}>
            <Text style={styles.fileName} numberOfLines={1}>
              {file.name}
            </Text>
            <Pressable
              onPress={() => setPicked((prev) => prev.filter((_, i) => i !== index))}
              disabled={busy}
            >
              <Text style={styles.remove}>Remove</Text>
            </Pressable>
          </View>
        ))}
        {uploadProgress != null ? (
          <UploadProgressBar
            progress={uploadProgress}
            label="Parking receipts"
            detail={uploadDetail}
          />
        ) : null}
        <Pressable
          style={[styles.submit, busy && styles.submitDisabled]}
          onPress={() => void submit()}
          disabled={busy}
        >
          {busy && uploadProgress == null ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.submitText}>
              {busy ? "Uploading…" : `Park ${picked.length || ""} file(s)`}
            </Text>
          )}
        </Pressable>
        <Text style={styles.caption}>
          Large files show progress. Each file is attempted separately with a sent /
          failed summary.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  root: { padding: space.lg, gap: space.md, backgroundColor: colors.bg, flexGrow: 1 },
  title: { ...type.title, color: colors.text },
  lead: { color: colors.muted, ...type.meta, lineHeight: 20 },
  row: { flexDirection: "row", gap: space.sm },
  btn: {
    flex: 1,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: "center",
  },
  btnText: { color: colors.text, fontWeight: "600", fontSize: 13 },
  fileRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: space.md,
    paddingVertical: 10,
    gap: space.sm,
  },
  fileName: { flex: 1, color: colors.text, fontSize: 13 },
  remove: { color: colors.danger, fontWeight: "600", fontSize: 12 },
  submit: {
    marginTop: space.sm,
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: "center",
  },
  submitDisabled: { opacity: 0.7 },
  submitText: { color: "#fff", ...type.button },
  caption: { color: colors.muted, ...type.caption },
});
