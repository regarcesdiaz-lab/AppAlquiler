import type { TextStyle } from 'react-native';

// Type scale from docs/BRAND.md. Brand font: Inter, weights 400 and 600 only.
// Each weight is its own font family (loaded in app/_layout.tsx); we select the weight through
// fontFamily rather than fontWeight so Android and iOS render the same face.
// Sizes scale with the OS font setting.

export const fontFamily = {
  regular: 'Inter_400Regular',
  semibold: 'Inter_600SemiBold',
} as const;

export const typography = {
  screenTitle: { fontFamily: fontFamily.semibold, fontSize: 28, lineHeight: 34 },
  sectionTitle: { fontFamily: fontFamily.semibold, fontSize: 20, lineHeight: 26 },
  body: { fontFamily: fontFamily.regular, fontSize: 17, lineHeight: 24 },
  bodyStrong: { fontFamily: fontFamily.semibold, fontSize: 17, lineHeight: 24 },
  secondary: { fontFamily: fontFamily.regular, fontSize: 15, lineHeight: 20 },
  secondaryStrong: { fontFamily: fontFamily.semibold, fontSize: 15, lineHeight: 20 },
  meta: { fontFamily: fontFamily.regular, fontSize: 13, lineHeight: 18 },
} as const satisfies Record<string, TextStyle>;

export const tabularNumbers: TextStyle = { fontVariant: ['tabular-nums'] };
