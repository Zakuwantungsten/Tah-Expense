import React from "react";
import { StyleSheet, View } from "react-native";
import { colors } from "../theme";

/**
 * Multi-layer swoosh under the login hero — same geometry idea as the fuel app,
 * tinted with our accent blues into the white form sheet.
 */
export function LoginWave() {
  return (
    <View style={styles.wrap} pointerEvents="none">
      <View style={[styles.layer, styles.deep]} />
      <View style={[styles.layer, styles.mid]} />
      <View style={[styles.layer, styles.light]} />
      <View style={[styles.layer, styles.sheet]} />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    height: 56,
    marginTop: -8,
    overflow: "hidden",
  },
  layer: {
    position: "absolute",
    left: -40,
    right: -40,
    height: 90,
    borderTopLeftRadius: 0,
    borderTopRightRadius: 0,
    borderBottomLeftRadius: 120,
    borderBottomRightRadius: 80,
  },
  deep: {
    top: 0,
    backgroundColor: colors.accentDark,
    transform: [{ scaleX: 1.15 }, { rotate: "-2deg" }],
  },
  mid: {
    top: 10,
    backgroundColor: colors.accent,
    opacity: 0.95,
    transform: [{ scaleX: 1.2 }, { rotate: "1.5deg" }],
  },
  light: {
    top: 22,
    backgroundColor: "#3B9FD9",
    opacity: 0.85,
    transform: [{ scaleX: 1.25 }, { rotate: "-1deg" }],
  },
  sheet: {
    top: 34,
    backgroundColor: colors.surface,
    borderBottomLeftRadius: 0,
    borderBottomRightRadius: 0,
    height: 80,
    transform: [{ scaleX: 1.3 }],
  },
});
