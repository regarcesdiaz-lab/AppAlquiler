import { useColorScheme } from 'react-native';

import { palette, statusPalette, type StatusColor, type StatusTone, type ThemeColors } from './colors';

export * from './colors';
export * from './spacing';
export * from './typography';

export function useThemeColors(): ThemeColors {
  return useColorScheme() === 'dark' ? palette.dark : palette.light;
}

export function useStatusColor(tone: StatusTone): StatusColor {
  return (useColorScheme() === 'dark' ? statusPalette.dark : statusPalette.light)[tone];
}
