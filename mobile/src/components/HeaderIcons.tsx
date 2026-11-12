import React from "react";
import { View } from "react-native";
import { colors } from "../theme";

/** Outline person — head + shoulders (no letter / no circle button). */
export function ProfileIcon({ color = colors.text, size = 22 }: { color?: string; size?: number }) {
  const head = Math.round(size * 0.34);
  const bodyW = Math.round(size * 0.72);
  const bodyH = Math.round(size * 0.42);
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "flex-end" }}>
      <View
        style={{
          width: head,
          height: head,
          borderRadius: head / 2,
          borderWidth: 1.8,
          borderColor: color,
          marginBottom: 2,
        }}
      />
      <View
        style={{
          width: bodyW,
          height: bodyH,
          borderTopLeftRadius: bodyW / 2,
          borderTopRightRadius: bodyW / 2,
          borderWidth: 1.8,
          borderBottomWidth: 0,
          borderColor: color,
        }}
      />
    </View>
  );
}

/** Outline bell for the notifications header control. */
export function BellIcon({ color = colors.text, size = 22 }: { color?: string; size?: number }) {
  const cup = Math.round(size * 0.55);
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }}>
      <View
        style={{
          width: cup,
          height: cup,
          borderTopLeftRadius: cup / 2,
          borderTopRightRadius: cup / 2,
          borderBottomLeftRadius: 3,
          borderBottomRightRadius: 3,
          borderWidth: 1.8,
          borderColor: color,
        }}
      />
      <View
        style={{
          width: Math.round(size * 0.72),
          height: 2,
          backgroundColor: color,
          marginTop: 1,
          borderRadius: 1,
        }}
      />
      <View
        style={{
          width: 4,
          height: 4,
          borderRadius: 2,
          backgroundColor: color,
          marginTop: 1,
        }}
      />
    </View>
  );
}
