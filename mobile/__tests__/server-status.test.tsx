import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, userEvent } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import ServerStatusScreen from '../app/index';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 },
};

function Providers({ children }: { children: ReactNode }) {
  // gcTime: Infinity avoids a pending garbage-collection timer keeping Jest alive.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  return (
    <QueryClientProvider client={client}>
      <SafeAreaProvider initialMetrics={metrics}>{children}</SafeAreaProvider>
    </QueryClientProvider>
  );
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const fetchMock = jest.fn<Promise<Response>, [Request]>();

beforeEach(() => {
  fetchMock.mockReset();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

test('shows each service status from /health', async () => {
  fetchMock.mockResolvedValue(
    jsonResponse({ status: 'ok', database: 'ok', redis: 'error', version: '0.1.0' }),
  );

  await render(<ServerStatusScreen />, { wrapper: Providers });

  expect(await screen.findByLabelText('Base de datos: Funciona')).toBeOnTheScreen();
  expect(screen.getByLabelText('Redis: Con fallas')).toBeOnTheScreen();
  expect(screen.getByText('Versión 0.1.0')).toBeOnTheScreen();
  expect(fetchMock.mock.calls[0][0].url).toMatch(/\/health$/);
});

test('shows a clear error and retries on demand', async () => {
  fetchMock.mockRejectedValueOnce(new TypeError('Network request failed'));
  fetchMock.mockResolvedValueOnce(
    jsonResponse({ status: 'ok', database: 'ok', redis: 'ok', version: '0.1.0' }),
  );

  await render(<ServerStatusScreen />, { wrapper: Providers });

  expect(await screen.findByText('No pudimos conectar')).toBeOnTheScreen();

  await userEvent.setup().press(screen.getByRole('button', { name: 'Reintentar' }));

  expect(await screen.findByLabelText('Redis: Funciona')).toBeOnTheScreen();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
