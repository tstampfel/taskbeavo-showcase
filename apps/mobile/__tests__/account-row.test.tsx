import { MockedProvider } from '@apollo/client/testing/react';
import { ViewerDocument } from '@taskbeavo/graphql';
import { catalogues } from '@taskbeavo/i18n';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { AccountRow } from '../src/components/account-row';
import { i18n } from '../src/i18n';

jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn() }) }));

const viewer = {
  __typename: 'User',
  id: 'u1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Kovač',
  locale: 'DE',
  country: 'DE',
  timeZone: 'Europe/Berlin',
  phoneVerified: true,
  phoneNumberMasked: '+49 ••• 678',
};
const viewerMock = {
  request: { query: ViewerDocument },
  result: { data: { viewer } },
  maxUsageCount: 5,
};

beforeAll(async () => {
  await i18n.changeLanguage('de');
});
afterAll(async () => {
  await i18n.changeLanguage('hr');
});

function renderRow(signOutEverywhere: () => Promise<void>) {
  return render(
    <MockedProvider mocks={[viewerMock]}>
      <AccountRow signOut={jest.fn(async () => undefined)} signOutEverywhere={signOutEverywhere} />
    </MockedProvider>,
  );
}

describe('AccountRow', () => {
  it('logs out of all devices', async () => {
    const signOutEverywhere = jest.fn(async () => undefined);
    await renderRow(signOutEverywhere);

    await fireEvent.press(await screen.findByText(catalogues.de.sessions.signOutEverywhere));

    await waitFor(() => expect(signOutEverywhere).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('sign-out-everywhere-failed')).toBeNull();
  });

  it('says so when logging out of all devices failed', async () => {
    await renderRow(jest.fn(async () => Promise.reject(new TypeError('offline'))));

    await fireEvent.press(await screen.findByTestId('sign-out-everywhere'));

    expect(
      await screen.findByText(catalogues.de.sessions.signOutEverywhereFailed),
    ).toBeOnTheScreen();
  });
});
