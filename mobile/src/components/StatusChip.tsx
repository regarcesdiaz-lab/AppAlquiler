import { StyleSheet, Text, View } from 'react-native';

import {
  borderWidth,
  radius,
  spacing,
  typography,
  useStatusColor,
  type StatusTone,
} from '@/theme';

type Props = {
  tone: StatusTone;
  label: string;
};

/** Color + text. The label is always visible: state is never shown by color alone. */
export function StatusChip({ tone, label }: Props) {
  const { text, background, border } = useStatusColor(tone);
  return (
    <View
      testID="status-chip"
      style={[
        styles.chip,
        { backgroundColor: background },
        border ? { borderColor: border, borderWidth: borderWidth.hairline } : null,
      ]}
    >
      <Text style={[typography.secondaryStrong, { color: text }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  chip: {
    borderRadius: radius.chip,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
    alignSelf: 'flex-start',
  },
});
