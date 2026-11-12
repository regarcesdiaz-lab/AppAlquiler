import { StyleSheet, Text, View } from 'react-native';

import { spacing, typography, useThemeColors } from '@/theme';

type Props = {
  title: string;
  subtitle?: string;
};

export function ScreenHeader({ title, subtitle }: Props) {
  const colors = useThemeColors();
  return (
    <View style={styles.container}>
      <Text accessibilityRole="header" style={[typography.screenTitle, { color: colors.text }]}>
        {title}
      </Text>
      {subtitle ? (
        <Text style={[typography.secondary, { color: colors.textMuted }]}>{subtitle}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: spacing.xs,
    marginBottom: spacing.xl,
  },
});
