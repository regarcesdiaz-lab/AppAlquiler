// Color tokens from docs/BRAND.md. Components never use raw color values.

export const palette = {
  light: {
    brand: '#0E5E6F',
    brandSoft: '#E6F2F4',
    bg: '#F7F7F5',
    surface: '#FFFFFF',
    border: '#E4E4E0',
    text: '#1A1D1D',
    textMuted: '#5E6666',
    onBrand: '#FFFFFF',
  },
  dark: {
    brand: '#5FB3C2',
    brandSoft: '#0F3540',
    bg: '#121414',
    surface: '#1C1F1F',
    border: '#2E3333',
    text: '#F2F2F0',
    textMuted: '#A3ABAB',
    onBrand: '#121414',
  },
} as const;

export type ColorScheme = keyof typeof palette;
export type ThemeColors = { [K in keyof (typeof palette)['light']]: string };

// Tenant status colors. Always shown together with a text label, never color alone.
// `border` is only set where the chip has no fill (vacant).
export type StatusColor = { text: string; background: string; border?: string };

export const statusPalette = {
  light: {
    overdue: { text: '#B42318', background: '#FEF3F2' },
    pending_confirmation: { text: '#B54708', background: '#FFFAEB' },
    promise: { text: '#475467', background: '#F2F4F7' },
    upcoming: { text: '#0E5E6F', background: '#E6F2F4' },
    paid: { text: '#067647', background: '#ECFDF3' },
    vacant: { text: '#5E6666', background: 'transparent', border: '#E4E4E0' },
  },
  dark: {
    overdue: { text: '#FDA29B', background: '#55160C' },
    pending_confirmation: { text: '#FEC84B', background: '#4E1D09' },
    promise: { text: '#D0D5DD', background: '#344054' },
    upcoming: { text: '#5FB3C2', background: '#0F3540' },
    paid: { text: '#75E0A7', background: '#053321' },
    vacant: { text: '#A3ABAB', background: 'transparent', border: '#2E3333' },
  },
} as const satisfies Record<ColorScheme, Record<string, StatusColor>>;

export type StatusTone = keyof (typeof statusPalette)['light'];
