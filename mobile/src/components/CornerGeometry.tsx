import React, { useMemo } from "react";
import { StyleSheet, View, type ViewStyle } from "react-native";
import { colors } from "../theme";

type Props = {
  /** Which corner anchors the pattern */
  corner?: "bottomLeft" | "bottomRight" | "topRight";
  /** Soft accent tint for lines/blocks */
  accent?: string;
  style?: ViewStyle;
};

/**
 * Decorative circuit-like geometry radiating from one corner toward center.
 * Pure Views (no SVG dependency) — light watermark for login / home.
 */
export function CornerGeometry({
  corner = "bottomLeft",
  accent = colors.accent,
  style,
}: Props) {
  const blocks = useMemo(
    () => [
      { size: 220, opacity: 0.07, rotate: "-18deg", offset: 0 },
      { size: 160, opacity: 0.09, rotate: "12deg", offset: 36 },
      { size: 110, opacity: 0.11, rotate: "-8deg", offset: 72 },
      { size: 70, opacity: 0.14, rotate: "22deg", offset: 104 },
    ],
    []
  );

  const anchor: ViewStyle =
    corner === "bottomLeft"
      ? { left: -40, bottom: -40 }
      : corner === "bottomRight"
        ? { right: -40, bottom: -40 }
        : { right: -40, top: -40 };

  return (
    <View pointerEvents="none" style={[styles.host, style]}>
      <View style={[styles.anchor, anchor]}>
        {blocks.map((b, i) => (
          <View
            key={i}
            style={[
              styles.block,
              {
                width: b.size,
                height: b.size,
                borderColor: accent,
                opacity: b.opacity,
                transform: [
                  { rotate: b.rotate },
                  { translateX: b.offset * (corner === "bottomLeft" ? 1 : -0.3) },
                  { translateY: b.offset * (corner.startsWith("bottom") ? -0.35 : 0.35) },
                ],
              },
            ]}
          />
        ))}
        {/* Trace lines toward center */}
        {[0, 1, 2, 3, 4].map((i) => (
          <View
            key={`line-${i}`}
            style={[
              styles.line,
              {
                width: 90 + i * 28,
                opacity: 0.08 + i * 0.02,
                backgroundColor: accent,
                transform: [
                  {
                    rotate:
                      corner === "bottomLeft"
                        ? `${-28 + i * 10}deg`
                        : corner === "bottomRight"
                          ? `${28 - i * 10}deg`
                          : `${48 - i * 8}deg`,
                  },
                ],
                ...(corner === "bottomLeft"
                  ? { left: 24 + i * 10, bottom: 48 + i * 14 }
                  : corner === "bottomRight"
                    ? { right: 24 + i * 10, bottom: 48 + i * 14 }
                    : { right: 24 + i * 10, top: 48 + i * 14 }),
              },
            ]}
          />
        ))}
        {/* Node dots */}
        {[0, 1, 2, 3].map((i) => (
          <View
            key={`dot-${i}`}
            style={[
              styles.dot,
              {
                backgroundColor: accent,
                opacity: 0.18,
                ...(corner === "bottomLeft"
                  ? { left: 50 + i * 42, bottom: 70 + i * 36 }
                  : corner === "bottomRight"
                    ? { right: 50 + i * 42, bottom: 70 + i * 36 }
                    : { right: 50 + i * 42, top: 70 + i * 36 }),
              },
            ]}
          />
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  host: {
    ...StyleSheet.absoluteFillObject,
    overflow: "hidden",
  },
  anchor: {
    position: "absolute",
    width: 320,
    height: 320,
  },
  block: {
    position: "absolute",
    borderWidth: 1.5,
    borderRadius: 18,
    left: 20,
    bottom: 20,
  },
  line: {
    position: "absolute",
    height: 2,
    borderRadius: 1,
  },
  dot: {
    position: "absolute",
    width: 8,
    height: 8,
    borderRadius: 4,
  },
});
