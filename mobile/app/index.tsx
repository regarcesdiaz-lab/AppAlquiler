import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { useHealth, type Health } from '@/api/health';
import { PrimaryButton } from '@/components/PrimaryButton';
import { ScreenHeader } from '@/components/ScreenHeader';
import { StatusChip } from '@/components/StatusChip';
import {
  borderWidth,
  layout,
  radius,
  spacing,
  tabularNumbers,
  typography,
  useThemeColors,
} from '@/theme';

export default function ServerStatusScreen() {
  const colors = useThemeColors();
  const health = useHealth();

  return (
    <SafeAreaView style={[styles.safeArea, { backgroundColor: colors.bg }]}>
      <ScrollView contentContainerStyle={styles.content}>
        <ScreenHeader title="Estado del servidor" subtitle="Comprueba que la app llega al servidor." />

        {health.isPending ? (
          <View style={styles.centered} accessibilityLiveRegion="polite">
            <ActivityIndicator color={colors.brand} accessibilityLabel="Cargando" />
            <Text style={[typography.body, { color: colors.textMuted }]}>Revisando el servidor…</Text>
          </View>
        ) : health.isError ? (
          <View
            style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}
            accessibilityLiveRegion="polite"
          >
            <Text style={[typography.sectionTitle, { color: colors.text }]}>
              No pudimos conectar
            </Text>
            <Text style={[typography.body, { color: colors.textMuted }]}>
              Revisa que el servidor esté encendido y que el teléfono esté en la misma red. Luego
              inténtalo de nuevo.
            </Text>
          </View>
        ) : (
          <HealthCard health={health.data} />
        )}

        <View style={styles.action}>
          <PrimaryButton
            label={health.isFetching && !health.isPending ? 'Revisando…' : 'Reintentar'}
            accessibilityHint="Vuelve a consultar el estado del servidor"
            disabled={health.isFetching}
            onPress={() => health.refetch()}
          />
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function HealthCard({ health }: { health: Health }) {
  const colors = useThemeColors();
  return (
    <View style={[styles.card, { backgroundColor: colors.surface, borderColor: colors.border }]}>
      <CheckRow label="Servidor" ok={health.status === 'ok'} />
      <CheckRow label="Base de datos" ok={health.database === 'ok'} />
      <CheckRow label="Redis" ok={health.redis === 'ok'} />
      <Text style={[typography.meta, tabularNumbers, { color: colors.textMuted }]}>
        Versión {health.version}
      </Text>
    </View>
  );
}

function CheckRow({ label, ok }: { label: string; ok: boolean }) {
  const colors = useThemeColors();
  const statusLabel = ok ? 'Funciona' : 'Con fallas';
  return (
    <View style={styles.row} accessible accessibilityLabel={`${label}: ${statusLabel}`}>
      <Text style={[typography.body, { color: colors.text }]}>{label}</Text>
      <StatusChip tone={ok ? 'paid' : 'overdue'} label={statusLabel} />
    </View>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
  },
  content: {
    padding: spacing.lg,
    width: '100%',
    maxWidth: layout.readingMaxWidth,
    alignSelf: 'center',
  },
  centered: {
    alignItems: 'center',
    gap: spacing.md,
    paddingVertical: spacing.xxl,
  },
  card: {
    borderWidth: borderWidth.hairline,
    borderRadius: radius.card,
    padding: spacing.lg,
    gap: spacing.md,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.md,
  },
  action: {
    marginTop: spacing.xl,
  },
});
