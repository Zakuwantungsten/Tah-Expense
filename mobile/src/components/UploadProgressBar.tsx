import React, { useEffect, useRef } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";
import { colors, space, type } from "../theme";

type Props = {
  /** 0–1 overall progress */
  progress: number;
  label?: string;
  detail?: string;
};

/** Sleek 0–100% upload bar — animated fill, large percent. */
export function UploadProgressBar({ progress, label, detail }: Props) {
  const pct = Math.max(0, Math.min(1, progress));
  const shown = Math.round(pct * 100);
  const width = useRef(new Animated.Value(pct)).current;

  useEffect(() => {
    Animated.timing(width, {
      toValue: pct,
      duration: 280,
      useNativeDriver: false,
    }).start();
  }, [pct, width]);

  return (
    <View style={styles.wrap}>
      <View style={styles.row}>
        <Text style={styles.label}>{label || "Uploading…"}</Text>
        <Text style={styles.pct}>{shown}%</Text>
      </View>
      <View style={styles.track}>
        <Animated.View
          style={[
            styles.fill,
            {
              width: width.interpolate({
                inputRange: [0, 1],
                outputRange: ["0%", "100%"],
              }),
            },
          ]}
        />
      </View>
      {detail ? <Text style={styles.detail}>{detail}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    marginTop: space.md,
    paddingVertical: space.md,
    paddingHorizontal: space.lg,
    backgroundColor: "#F3F8FC",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#C9E0F2",
  },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
    marginBottom: 10,
  },
  label: { ...type.subtitle, color: colors.text, fontSize: 13, flex: 1 },
  pct: {
    color: colors.accent,
    fontSize: 22,
    fontWeight: "700",
    letterSpacing: 0.4,
    marginLeft: space.sm,
  },
  track: {
    height: 10,
    borderRadius: 5,
    backgroundColor: "#E2EBF3",
    overflow: "hidden",
  },
  fill: {
    height: "100%",
    borderRadius: 5,
    backgroundColor: colors.accent,
  },
  detail: { marginTop: 10, ...type.caption, color: colors.muted },
});
