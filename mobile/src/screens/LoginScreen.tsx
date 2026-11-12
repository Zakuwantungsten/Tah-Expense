import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { API_BASE_URL } from "../config";
import { useAuth } from "../auth/AuthContext";
import { loadRemembered } from "../auth/remember";
import { LoginWave } from "../components/LoginWave";
import { colors, space, type } from "../theme";

export function LoginScreen() {
  const { signIn, error, clearError } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      const saved = await loadRemembered();
      if (!alive) {
        return;
      }
      setRemember(saved.remember);
      if (saved.username) {
        setUsername(saved.username);
      }
      if (saved.password) {
        setPassword(saved.password);
      }
      setHydrated(true);
    })();
    return () => {
      alive = false;
    };
  }, []);

  const onSubmit = async () => {
    if (!username.trim() || !password || busy) {
      return;
    }
    setBusy(true);
    clearError();
    try {
      await signIn(username, password, remember);
    } catch {
      // error already on context
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.root}>
      <SafeAreaView style={styles.heroSafe} edges={["top"]}>
        <View style={styles.hero}>
          <Image
            source={require("../../assets/logo_blue.png")}
            style={styles.brandLogo}
            resizeMode="contain"
            tintColor="#FFFFFF"
          />
          <Text style={styles.welcome}>Welcome back</Text>
          <Text style={styles.heroHint}>
            Sign in to capture and send receipts for your open requests.
          </Text>
        </View>
      </SafeAreaView>
      <LoginWave />

      <KeyboardAvoidingView
        style={styles.formWrap}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.formScroll}
          keyboardShouldPersistTaps="handled"
          bounces={false}
        >
          <Text style={styles.label}>Username</Text>
          <View style={styles.inputShell}>
            <Text style={styles.inputIcon}>◉</Text>
            <TextInput
              autoCapitalize="none"
              autoCorrect={false}
              style={styles.input}
              value={username}
              onChangeText={setUsername}
              placeholder="Enter your username"
              placeholderTextColor={colors.muted}
              editable={!busy && hydrated}
            />
          </View>

          <Text style={styles.label}>Password</Text>
          <View style={styles.inputShell}>
            <Text style={styles.inputIcon}>◌</Text>
            <TextInput
              secureTextEntry={!showPassword}
              style={styles.input}
              value={password}
              onChangeText={setPassword}
              placeholder="Enter your password"
              placeholderTextColor={colors.muted}
              editable={!busy && hydrated}
              onSubmitEditing={() => void onSubmit()}
            />
            <Pressable
              onPress={() => setShowPassword((v) => !v)}
              hitSlop={8}
              style={styles.eyeBtn}
            >
              <Text style={styles.eyeText}>{showPassword ? "Hide" : "Show"}</Text>
            </Pressable>
          </View>

          <Pressable
            style={styles.rememberRow}
            onPress={() => setRemember((v) => !v)}
            disabled={busy}
            hitSlop={6}
          >
            <View style={[styles.checkbox, remember && styles.checkboxOn]}>
              {remember ? <Text style={styles.checkMark}>✓</Text> : null}
            </View>
            <Text style={styles.rememberTitle}>Remember me</Text>
          </Pressable>

          {error ? <Text style={styles.error}>{error}</Text> : null}

          <Pressable
            style={[styles.button, busy && styles.buttonDisabled]}
            onPress={() => void onSubmit()}
            disabled={busy || !hydrated}
          >
            {busy ? (
              <ActivityIndicator color="#FFFFFF" />
            ) : (
              <Text style={styles.buttonText}>Sign in</Text>
            )}
          </Pressable>

          <Pressable
            onPress={() => setShowAdvanced((v) => !v)}
            style={styles.advancedToggle}
            hitSlop={8}
          >
            <Text style={styles.advancedText}>
              {showAdvanced ? "Hide connection" : "Advanced — API connection"}
            </Text>
          </Pressable>
          {showAdvanced ? (
            <Text style={styles.api} selectable>
              {API_BASE_URL}
            </Text>
          ) : null}

          <Text style={styles.footer}>
            Stay signed in on this phone · refresh keeps your session alive
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.surface },
  heroSafe: { backgroundColor: colors.hero },
  hero: {
    backgroundColor: colors.hero,
    paddingHorizontal: space.xl,
    paddingTop: space.lg,
    paddingBottom: space.md,
    alignItems: "center",
  },
  brandLogo: {
    width: 280,
    height: 200,
    marginBottom: space.sm,
  },
  welcome: {
    marginTop: space.md,
    fontSize: 28,
    fontWeight: "800",
    color: "#FFFFFF",
    letterSpacing: -0.5,
    textAlign: "center",
  },
  heroHint: {
    marginTop: space.sm,
    color: colors.heroMuted,
    ...type.meta,
    lineHeight: 20,
    textAlign: "center",
    maxWidth: 320,
    paddingHorizontal: space.md,
  },
  formWrap: { flex: 1, backgroundColor: colors.surface },
  formScroll: {
    paddingHorizontal: space.xl,
    paddingTop: space.lg,
    paddingBottom: space.xl,
  },
  label: {
    marginTop: space.md,
    marginBottom: 6,
    color: colors.text,
    fontSize: 13,
    fontWeight: "700",
  },
  inputShell: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 14,
    backgroundColor: colors.surface,
    paddingHorizontal: 12,
    minHeight: 52,
  },
  inputIcon: {
    color: colors.muted,
    fontSize: 14,
    marginRight: 8,
    width: 18,
    textAlign: "center",
  },
  input: {
    flex: 1,
    fontSize: 16,
    color: colors.text,
    paddingVertical: Platform.OS === "ios" ? 14 : 10,
  },
  eyeBtn: { paddingLeft: 8, paddingVertical: 6 },
  eyeText: { color: colors.accent, fontWeight: "700", fontSize: 12 },
  rememberRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginTop: space.lg,
  },
  checkbox: {
    width: 22,
    height: 22,
    borderRadius: 6,
    borderWidth: 1.5,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surface,
  },
  checkboxOn: {
    backgroundColor: colors.accent,
    borderColor: colors.accent,
  },
  checkMark: { color: "#FFFFFF", fontSize: 13, fontWeight: "800" },
  rememberTitle: { ...type.subtitle, color: colors.text, fontSize: 14 },
  error: {
    marginTop: 12,
    color: colors.danger,
    fontSize: 13,
  },
  button: {
    marginTop: space.xl,
    backgroundColor: colors.accent,
    borderRadius: 14,
    paddingVertical: 15,
    alignItems: "center",
  },
  buttonDisabled: {
    opacity: 0.7,
  },
  buttonText: {
    color: "#FFFFFF",
    ...type.button,
    fontSize: 16,
  },
  advancedToggle: {
    marginTop: space.lg,
    alignItems: "center",
  },
  advancedText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600",
  },
  api: {
    marginTop: 8,
    color: colors.muted,
    fontSize: 11,
    textAlign: "center",
  },
  footer: {
    marginTop: space.xl,
    textAlign: "center",
    color: colors.muted,
    ...type.caption,
    lineHeight: 16,
  },
});
