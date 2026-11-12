import createClient from 'openapi-fetch';

import type { paths } from './schema';

// EXPO_PUBLIC_* values are embedded in the app bundle: never put secrets here.
export const API_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

export const api = createClient<paths>({
  baseUrl: API_URL,
  // Resolve fetch at call time (not at import) so tests and polyfills can replace it.
  fetch: (request) => globalThis.fetch(request),
});

const REQUEST_TIMEOUT_MS = 8000;

/**
 * Runs `request` with a signal that aborts after a timeout or when `signal` aborts.
 * The timer is always cleared once the request settles.
 */
export async function withTimeout<T>(
  request: (signal: AbortSignal) => Promise<T>,
  signal?: AbortSignal,
  ms = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  const timer = setTimeout(abort, ms);
  signal?.addEventListener('abort', abort);
  try {
    return await request(controller.signal);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}
