/** Shared visual tokens — Office-adjacent blue, calm surfaces. */

export const colors = {
  bg: "#EEF2F6",
  surface: "#FFFFFF",
  border: "#E2E8F0",
  text: "#0F172A",
  muted: "#64748B",
  accent: "#0077C5",
  accentDark: "#005EA3",
  /** Login hero (navy) — pairs with accent wave like fuel-app geometry */
  hero: "#0B1F33",
  heroMuted: "#94A3B8",
  danger: "#B91C1C",
  warn: "#B45309",
  ok: "#15803D",
  kpiOpen: "#0077C5",
  kpiOverdue: "#B91C1C",
  kpiReminded: "#B45309",
} as const;

export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
} as const;

export const type = {
  brand: { fontSize: 28, fontWeight: "800" as const, letterSpacing: -0.4 },
  title: { fontSize: 20, fontWeight: "700" as const },
  subtitle: { fontSize: 14, fontWeight: "600" as const },
  body: { fontSize: 15, fontWeight: "400" as const },
  meta: { fontSize: 13, fontWeight: "400" as const },
  caption: { fontSize: 11, fontWeight: "500" as const },
  button: { fontSize: 15, fontWeight: "700" as const },
} as const;
