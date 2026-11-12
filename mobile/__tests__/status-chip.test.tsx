import { render, screen } from '@testing-library/react-native';

import { StatusChip } from '@/components/StatusChip';
import { statusPalette } from '@/theme';

const mockColorScheme = jest.fn<'light' | 'dark', []>(() => 'light');
jest.mock('react-native/Libraries/Utilities/useColorScheme', () => ({
  __esModule: true,
  default: () => mockColorScheme(),
}));

afterEach(() => {
  mockColorScheme.mockReset();
});

test('every status has text and background colors in both modes', () => {
  for (const scheme of ['light', 'dark'] as const) {
    for (const [tone, color] of Object.entries(statusPalette[scheme])) {
      expect({ scheme, tone, text: color.text }).toEqual({
        scheme,
        tone,
        text: expect.stringMatching(/^#[0-9A-F]{6}$/),
      });
      expect(color.background).toBeTruthy();
    }
  }
});

test('uses light status colors in light mode', async () => {
  mockColorScheme.mockReturnValue('light');

  await render(<StatusChip tone="overdue" label="Atrasado" />);

  expect(screen.getByText('Atrasado')).toHaveStyle({ color: '#B42318' });
  expect(screen.getByTestId('status-chip')).toHaveStyle({ backgroundColor: '#FEF3F2' });
});

test('uses dark status colors in dark mode', async () => {
  mockColorScheme.mockReturnValue('dark');

  await render(<StatusChip tone="overdue" label="Atrasado" />);

  expect(screen.getByText('Atrasado')).toHaveStyle({ color: '#FDA29B' });
  expect(screen.getByTestId('status-chip')).toHaveStyle({ backgroundColor: '#55160C' });
});

test('vacant chip has no fill and a visible border', async () => {
  mockColorScheme.mockReturnValue('dark');

  await render(<StatusChip tone="vacant" label="Vacío" />);

  expect(screen.getByTestId('status-chip')).toHaveStyle({
    backgroundColor: 'transparent',
    borderColor: '#2E3333',
    borderWidth: 1,
  });
});
