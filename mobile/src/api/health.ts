import { useQuery } from '@tanstack/react-query';

import { api, withTimeout } from './client';
import type { components } from './schema';

export type Health = components['schemas']['HealthResponse'];

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const { data, error } = await withTimeout((s) => api.GET('/health', { signal: s }), signal);
  if (error || !data) {
    throw new Error('Health check failed');
  }
  return data;
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
  });
}
