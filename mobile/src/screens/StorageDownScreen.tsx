import React from "react";
import {
  Linking,
  Pressable,
  SafeAreaView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { colors, space, type } from "../theme";

type Props = {
  message?: string | null;
  hint?: string | null;
  onRetry?: () => void;
};

export function StorageDownScreen({ message, hint, onRetry }: Props) {
  const body =
    hint?.trim() ||
    "We are experiencing receipt storage issues. Please send receipts the usual way through WhatsApp until this is fixed.";
  const detail = message?.trim();

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.card}>
        <Text style={styles.kicker}>Receipt uploads paused</Text>
        <Text style={styles.title}>Use WhatsApp for now</Text>
        <Text style={styles.body}>{body}</Text>
        {detail ? <Text style={styles.detail}>{detail}</Text> : null}
        <Pressable
          style={styles.primary}
          onPress={() => {
            void Linking.openURL("whatsapp://send").catch(() => {
              void Linking.openURL("https://whatsapp.com/");
            });
          }}
        >
          <Text style={styles.primaryText}>Open WhatsApp</Text>
        </Pressable>
        {onRetry ? (
          <Pressable style={styles.secondary} onPress={onRetry}>
            <Text style={styles.secondaryText}>Check again</Text>
          </Pressable>
        ) : null}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: colors.bg,
    justifyContent: "center",
    padding: space.xl,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#FECACA",
    padding: space.xl,
  },
  kicker: {
    ...type.caption,
    color: colors.danger,
    textTransform: "uppercase",
    letterSpacing: 0.6,
    marginBottom: space.sm,
  },
  title: {
    ...type.title,
    color: colors.text,
    marginBottom: space.md,
  },
  body: {
    ...type.body,
    color: colors.text,
    lineHeight: 22,
    marginBottom: space.md,
  },
  detail: {
    ...type.meta,
    color: colors.muted,
    marginBottom: space.lg,
  },
  primary: {
    backgroundColor: "#128C7E",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
  },
  primaryText: {
    ...type.button,
    color: "#FFFFFF",
  },
  secondary: {
    marginTop: space.md,
    paddingVertical: 12,
    alignItems: "center",
  },
  secondaryText: {
    ...type.subtitle,
    color: colors.accent,
  },
});
