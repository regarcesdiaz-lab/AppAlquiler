// Spacing, shape and touch tokens from docs/BRAND.md.

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
  xxxl: 48,
} as const;

export const radius = {
  control: 8,
  card: 12,
  chip: 999,
} as const;

export const touch = {
  minSize: 48,
  minGap: 8,
} as const;

export const layout = {
  readingMaxWidth: 720,
  tabletBreakpoint: 600,
} as const;

export const borderWidth = {
  hairline: 1,
} as const;
