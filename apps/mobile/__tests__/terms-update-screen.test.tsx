import { MockedProvider } from '@apollo/client/testing/react';
import {
  AcceptCurrentTermsDocument,
  TermsStatusDocument,
  TermsToAcceptDocument,
  ViewerDocument,
} from '@taskbeavo/graphql';
import { catalogues } from '@taskbeavo/i18n';
import { colors, minTapTarget, typography } from '@taskbeavo/ui-tokens';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react-native';
import { Dimensions, StyleSheet } from 'react-native';
import { GraphQLError } from 'graphql';
import {
  markTermsUpdateShown,
  resetTermsBannerForTests,
  TermsBanner,
} from '../src/components/terms-banner';
import { i18n } from '../src/i18n';
import { TermsUpdateScreen } from '../src/screens/terms-update-screen';

const mockPush = jest.fn();
jest.mock('expo-router', () => ({ useRouter: () => ({ push: mockPush }) }));

const de = catalogues.de.termsUpdate;
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
const status = (mustAcceptTerms: boolean, version = '2026-12', language = 'DE') => ({
  request: { query: TermsStatusDocument, variables: { language } },
  result: {
    data: {
      viewer: {
        __typename: 'User',
        id: 'u1',
        mustAcceptTerms,
        termsToAccept: mustAcceptTerms
          ? { __typename: 'LegalDocument', id: `t-TERMS-${version}`, version }
          : null,
      },
    },
  },
});
const terms = (version: string, kind = 'TERMS', language = 'DE') => ({
  request: { query: TermsToAcceptDocument, variables: { language } },
  result: {
    data: {
      viewer: {
        __typename: 'User',
        id: 'u1',
        termsToAccept: {
          __typename: 'LegalDocument',
          id: `t-${kind}-${version}`,
          kind,
          version,
          language,
          effectiveAt: '2026-12-01T00:00:00Z',
        },
      },
    },
  },
});
const accepted = {
  request: { query: AcceptCurrentTermsDocument, variables: { version: '2026-12', language: 'DE' } },
  result: {
    data: {
      acceptCurrentTerms: { ...viewer, accountState: 'ACTIVE', mustAcceptTerms: false },
    },
  },
};

beforeAll(async () => {
  await i18n.changeLanguage('de');
});
afterAll(async () => {
  await i18n.changeLanguage('hr');
});
beforeEach(() => {
  mockPush.mockClear();
  resetTermsBannerForTests();
});

function renderScreen(mocks: object[], props: Partial<Parameters<typeof TermsUpdateScreen>[0]>) {
  return render(
    <MockedProvider mocks={mocks as never}>
      <TermsUpdateScreen
        onDone={jest.fn()}
        onSignedOut={jest.fn()}
        openLegal={jest.fn()}
        {...props}
      />
    </MockedProvider>,
  );
}

describe('TermsUpdateScreen', () => {
  it('accepts the version shown, in the app language, only once the box is ticked', async () => {
    const onDone = jest.fn();
    await renderScreen([status(true), terms('2026-12'), accepted, status(false)], { onDone });

    expect(await screen.findByTestId('terms-update-version')).toHaveTextContent(
      /2026-12.*1\. Dezember 2026/,
    );
    const accept = screen.getByTestId('terms-update-accept');
    expect(accept).toBeDisabled();
    expect(screen.getByText(de.tickToAccept)).toBeOnTheScreen();

    await fireEvent.press(screen.getByTestId('terms-update-agree'));
    await fireEvent.press(accept);

    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it('unticks the box and loads the new version when the terms changed meanwhile', async () => {
    const refused = {
      request: accepted.request,
      result: { errors: [new GraphQLError('x', { extensions: { code: 'TERMS_NOT_ACCEPTED' } })] },
    };
    const onDone = jest.fn();
    await renderScreen([status(true), terms('2026-12'), refused, terms('2027-01')], { onDone });

    await fireEvent.press(await screen.findByTestId('terms-update-agree'));
    await fireEvent.press(screen.getByTestId('terms-update-accept'));

    expect(await screen.findByText(catalogues.de.errors.TERMS_NOT_ACCEPTED)).toBeOnTheScreen();
    await waitFor(() =>
      expect(screen.getByTestId('terms-update-agree')).toHaveTextContent(/Version 2027-01/),
    );
    expect(screen.getByTestId('terms-update-agree')).toHaveProp('accessibilityState', {
      checked: false,
    });
    expect(onDone).not.toHaveBeenCalled();
  });

  it('"Not now" leaves without accepting', async () => {
    const onDone = jest.fn();
    await renderScreen([status(true), terms('2026-12')], { onDone });

    await fireEvent.press(await screen.findByTestId('terms-update-not-now'));

    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('carries on when there is nothing to accept', async () => {
    const onDone = jest.fn();
    await renderScreen([status(false), terms('2026-12')], { onDone });

    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it('says so when the status check fails, with Try again and Not now', async () => {
    const failed = {
      request: { query: TermsStatusDocument, variables: { language: 'DE' } },
      error: new TypeError('Network request failed'),
    };
    const onDone = jest.fn();
    await renderScreen([failed, terms('2026-12'), status(true)], { onDone });

    expect(await screen.findByText(de.unavailable)).toBeOnTheScreen();
    await fireEvent.press(screen.getByTestId('terms-update-status-retry'));
    expect(await screen.findByTestId('terms-update-agree')).toBeOnTheScreen();
    expect(onDone).not.toHaveBeenCalled();
  });

  it('"Not now" works from the failed status check too', async () => {
    const failed = {
      request: { query: TermsStatusDocument, variables: { language: 'DE' } },
      error: new TypeError('Network request failed'),
    };
    const onDone = jest.fn();
    await renderScreen([failed, terms('2026-12')], { onDone });

    await fireEvent.press(await screen.findByTestId('terms-update-not-now'));

    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('opens the full terms in the app language', async () => {
    const openLegal = jest.fn();
    await renderScreen([status(true), terms('2026-12')], { openLegal });

    await fireEvent.press(await screen.findByTestId('terms-update-read'));

    expect(openLegal).toHaveBeenCalledWith('terms', 'de');
  });

  it('names the version and date on the tick box, and accepts that version', async () => {
    const onDone = jest.fn();
    await renderScreen([status(true), terms('2026-12'), accepted, status(false)], { onDone });

    const box = await screen.findByTestId('terms-update-agree');
    expect(box).toHaveTextContent(
      'Ich habe die aktualisierten AGB (Version 2026-12, gültig ab 1. Dezember 2026) gelesen und stimme ihnen zu.',
    );
    expect(box).toHaveProp(
      'accessibilityLabel',
      'Ich habe die aktualisierten AGB (Version 2026-12, gültig ab 1. Dezember 2026) gelesen und stimme ihnen zu.',
    );
    expect(screen.getByTestId('terms-update-bookings')).toHaveTextContent(
      /vor dem 1\. Dezember 2026 angenommen/,
    );
    await fireEvent.press(box);
    await fireEvent.press(screen.getByTestId('terms-update-accept'));

    // The `accepted` mock only answers version 2026-12: the version the box named.
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it.each([
    ['hr', 'HR', 'Ako se ne slažete'],
    ['en', 'EN', "If you don't agree"],
    ['de', 'DE', 'Wenn Sie nicht zustimmen'],
    ['fr', 'FR', 'Si vous n’êtes pas d’accord'],
  ])('shows the refusal line in %s, opening the Privacy notice', async (lng, api, text) => {
    await i18n.changeLanguage(lng);
    const openLegal = jest.fn();
    const shown = await renderScreen(
      [status(true, '2026-12', api), terms('2026-12', 'TERMS', api)],
      {
        openLegal,
      },
    );
    try {
      expect(await screen.findByTestId('terms-update-refuse')).toHaveTextContent(new RegExp(text));
      await fireEvent.press(screen.getByTestId('terms-update-close'));
      expect(openLegal).toHaveBeenCalledWith('privacy', lng);
    } finally {
      // Unmounted first, so switching back doesn't re-render it outside act().
      await shown.unmount();
      await i18n.changeLanguage('de');
    }
  });

  it('shows no tick box and no half-filled version while the terms are unavailable', async () => {
    const missing = {
      request: { query: TermsToAcceptDocument, variables: { language: 'DE' } },
      result: { data: { viewer: { __typename: 'User', id: 'u1', termsToAccept: null } } },
    };
    await renderScreen([status(true), missing], {});

    expect(await screen.findByText(de.unavailable)).toBeOnTheScreen();
    expect(screen.getByTestId('terms-update-retry')).toBeOnTheScreen();
    expect(screen.queryByTestId('terms-update-agree')).toBeNull();
    expect(screen.queryByTestId('terms-update-version')).toBeNull();
    expect(screen.queryByText(/Version ,|gültig ab \)/)).toBeNull();
    expect(screen.getByTestId('terms-update-accept')).toBeDisabled();
  });

  it('shows and opens the Helper terms to a helper re-accepting HELPER_TERMS', async () => {
    const openLegal = jest.fn();
    await renderScreen([status(true), terms('2027-01', 'HELPER_TERMS')], { openLegal });

    const box = await screen.findByTestId('terms-update-agree');
    expect(box).toHaveTextContent(/aktualisierten Bedingungen für Helfer \(Version 2027-01/);
    expect(screen.getByTestId('terms-update-version')).toHaveTextContent(/Bedingungen für Helfer/);
    expect(screen.getByTestId('terms-update-read')).toHaveTextContent(
      'Vollständige Bedingungen für Helfer lesen',
    );
    await fireEvent.press(screen.getByTestId('terms-update-read'));
    await fireEvent.press(screen.getByTestId('terms-update-agree-link'));

    expect(openLegal.mock.calls).toEqual([
      ['helper-terms', 'de'],
      ['helper-terms', 'de'],
    ]);
  });
});

describe('TermsBanner', () => {
  // A fresh mock per render: MockedProvider uses each one up.
  const viewerMock = () => ({ request: { query: ViewerDocument }, result: { data: { viewer } } });

  it('opens the screen once per launch, then explains limited mode', async () => {
    await render(
      <MockedProvider mocks={[viewerMock(), status(true)]}>
        <TermsBanner />
      </MockedProvider>,
    );

    expect(await screen.findByText(de.bannerTitle)).toBeOnTheScreen();
    expect(mockPush).toHaveBeenCalledTimes(1);
    await fireEvent.press(screen.getByTestId('terms-banner-review'));
    expect(mockPush).toHaveBeenLastCalledWith('/terms-update');
  });

  it("doesn't open the screen again when the launch opened it first", async () => {
    const opened = await renderScreen([status(true), terms('2026-12')], {});
    await screen.findByTestId('terms-update-agree');
    await opened.unmount();

    await render(
      <MockedProvider mocks={[viewerMock(), status(true)]}>
        <TermsBanner />
      </MockedProvider>,
    );

    expect(await screen.findByText(de.bannerTitle)).toBeOnTheScreen();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('opens it again for another account signing in', async () => {
    markTermsUpdateShown('u2', '2026-12');

    await render(
      <MockedProvider mocks={[viewerMock(), status(true)]}>
        <TermsBanner />
      </MockedProvider>,
    );

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/terms-update'));
  });

  it('opens it again when a newer version takes effect in the same launch', async () => {
    markTermsUpdateShown('u1', '2026-12');

    const same = await render(
      <MockedProvider mocks={[viewerMock(), status(true, '2026-12')]}>
        <TermsBanner />
      </MockedProvider>,
    );
    expect(await screen.findByText(de.bannerTitle)).toBeOnTheScreen();
    expect(mockPush).not.toHaveBeenCalled();
    await same.unmount();

    await render(
      <MockedProvider mocks={[viewerMock(), status(true, '2027-01')]}>
        <TermsBanner />
      </MockedProvider>,
    );

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/terms-update'));
    expect(mockPush).toHaveBeenCalledTimes(1);
  });

  it('shows nothing when the terms are accepted', async () => {
    await render(
      <MockedProvider mocks={[viewerMock(), status(false)]}>
        <TermsBanner />
      </MockedProvider>,
    );

    await waitFor(() => expect(screen.queryByTestId('terms-banner')).toBeNull());
    expect(mockPush).not.toHaveBeenCalled();
  });
});

// BEAV-215: mockup 21b.
describe('TermsUpdateScreen design', () => {
  it('has no top bar, and a white card headed by small capitals with ticked lines', async () => {
    await renderScreen([status(true), terms('2026-12')], {});
    const card = await screen.findByTestId('terms-update-changes');
    expect(screen.queryByTestId('screen-back')).toBeNull();
    // Raised (a soft shadow), not outlined.
    expect(StyleSheet.flatten(card.props.style).borderWidth).toBeUndefined();
    const label = screen.getByRole('header', { name: de.whatsChanged });
    // 21b draws the label inside the card in ink, not muted like a label above a card.
    expect(StyleSheet.flatten(label.props.style)).toMatchObject({
      textTransform: 'uppercase',
      color: colors.ink,
    });
    expect(
      within(card).getAllByTestId('terms-update-tick', { includeHiddenElements: true }),
    ).toHaveLength(2);
  });

  it('shows "Not now" as a small button that is still a 44pt target', async () => {
    await renderScreen([status(true), terms('2026-12')], {});
    await screen.findByTestId('terms-update-changes');
    const notNow = screen.getByRole('button', { name: de.notNow });
    expect(StyleSheet.flatten(within(notNow).getByText(de.notNow).props.style).fontSize).toBe(
      typography.small.fontSize,
    );
    expect(Number(StyleSheet.flatten(notNow.props.style).minHeight)).toBeGreaterThanOrEqual(
      minTapTarget,
    );
  });

  it('keeps each tick on the first line of its text at large text sizes', async () => {
    const dimensions = jest
      .spyOn(Dimensions, 'get')
      .mockReturnValue({ width: 402, height: 874, scale: 3, fontScale: 3 });
    try {
      await renderScreen([status(true), terms('2026-12')], {});
      await screen.findByTestId('terms-update-changes');
      const [tick] = screen.getAllByTestId('terms-update-tick', { includeHiddenElements: true });
      // The line's text grows only as far as its longest word fits (BEAV-223): the tick follows
      // the size the text renders at, not the raw font scale.
      const row = tick!.parent!;
      const line = row.children.find((child) => typeof child !== 'string' && child !== tick);
      const rendered = Math.min(3, (line as typeof tick)!.props.maxFontSizeMultiplier as number);
      expect(rendered).toBeGreaterThanOrEqual(1);
      expect(StyleSheet.flatten(tick!.props.style).height).toBe(
        typography.small.lineHeight * rendered,
      );
    } finally {
      dimensions.mockRestore();
    }
  });
});
