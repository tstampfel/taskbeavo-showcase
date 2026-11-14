import { MockedProvider } from '@apollo/client/testing/react';
import { ConsentsDocument, SetConsentDocument } from '@taskbeavo/graphql';
import { catalogues } from '@taskbeavo/i18n';
import { colors } from '@taskbeavo/ui-tokens';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react-native';
import { StyleSheet } from 'react-native';
import { fonts } from '../src/fonts';
import { GraphQLError } from 'graphql';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { i18n } from '../src/i18n';
import { PrivacyScreen } from '../src/screens/privacy-screen';
import { SettingsScreen } from '../src/screens/settings-screen';

const en = catalogues.en;
const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};
const PURPOSES = ['ANALYTICS', 'MARKETING_EMAIL', 'MARKETING_PUSH'] as const;

const state = (purpose: string, granted: boolean) => ({
  __typename: 'ConsentState',
  id: purpose,
  purpose,
  granted,
  version: granted ? 'privacy-1' : null,
  givenAt: granted ? '2026-11-13T10:00:00Z' : null,
});
const allOff = {
  request: { query: ConsentsDocument },
  result: { data: { consents: PURPOSES.map((purpose) => state(purpose, false)) } },
};
const setMock = (purpose: string, granted: boolean, result: Record<string, unknown>) => ({
  request: { query: SetConsentDocument, variables: { purpose, granted, language: 'EN' } },
  result,
});

async function renderScreen(mocks: unknown[]) {
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <MockedProvider mocks={mocks as never}>
        <PrivacyScreen onBack={jest.fn()} />
      </MockedProvider>
    </SafeAreaProvider>,
  );
}

describe('Settings → Privacy and data', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it('opens from Settings', async () => {
    const onPrivacy = jest.fn();
    await render(
      <SettingsScreen
        onBack={jest.fn()}
        onBusiness={jest.fn()}
        onPrivacy={onPrivacy}
        onLegal={jest.fn()}
      />,
    );

    await fireEvent.press(screen.getByRole('button', { name: en.privacySettings.title }));
    expect(onPrivacy).toHaveBeenCalled();
  });

  it('shows analytics and marketing off until the user agrees', async () => {
    await renderScreen([allOff]);

    for (const purpose of PURPOSES) {
      expect(await screen.findByTestId(`privacy-${purpose}`)).toHaveProp('value', false);
    }
    expect(screen.getByText(en.privacySettings.analyticsApp)).toBeOnTheScreen();
    expect(screen.getByText(en.privacySettings.footnote)).toBeOnTheScreen();
  });

  it('logs a switch change as a consent', async () => {
    const saved = jest.fn(() => ({ data: { setConsent: state('MARKETING_PUSH', true) } }));
    await renderScreen([allOff, { ...setMock('MARKETING_PUSH', true, {}), result: saved }]);

    await fireEvent(await screen.findByTestId('privacy-MARKETING_PUSH'), 'valueChange', true);

    await waitFor(() => expect(saved).toHaveBeenCalled());
    expect(screen.getByTestId('privacy-MARKETING_PUSH')).toHaveProp('value', true);
  });

  it('rolls the switch back and explains a refusal', async () => {
    await renderScreen([
      allOff,
      setMock('ANALYTICS', true, {
        errors: [new GraphQLError('refused', { extensions: { code: 'CONSENT_UNAVAILABLE' } })],
      }),
    ]);

    await fireEvent(await screen.findByTestId('privacy-ANALYTICS'), 'valueChange', true);

    expect(await screen.findByText(en.errors.CONSENT_UNAVAILABLE)).toBeOnTheScreen();
    expect(screen.getByTestId('privacy-ANALYTICS')).toHaveProp('value', false);
  });
});

// BEAV-215: the switches sit in one list card (mockup 19a).
describe('Privacy and data design', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it('draws the three switches in one card, bold labels and muted descriptions', async () => {
    await renderScreen([allOff]);
    const list = await screen.findByTestId('privacy-consents');
    expect(within(list).getAllByRole('switch')).toHaveLength(3);
    expect(
      StyleSheet.flatten(screen.getByText(en.privacySettings.analytics).props.style),
    ).toMatchObject({
      color: colors.ink,
      fontFamily: fonts.bold,
    });
    expect(
      StyleSheet.flatten(screen.getByText(en.privacySettings.analyticsApp).props.style).color,
    ).toBe(colors.inkMuted);
  });
});
