import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { typePolicies } from '@taskbeavo/graphql';
import { catalogues, formatMoney, locales } from '@taskbeavo/i18n';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react-native';
import { Dimensions, StyleSheet } from 'react-native';
import { Observable } from 'rxjs';
import { i18n } from '../src/i18n';
import { OffersScreen } from '../src/screens/offers-screen';

const TASK = 'task-1';
const NOW = new Date('2026-11-05T10:00:00Z');

// 12%, at least €2.50, as the API computes it from the price list in force.
function fee(priceCents: number) {
  return Math.max(250, Math.round(priceCents * 0.12));
}

function offer(id: string, extra: Record<string, unknown> = {}) {
  const priceCents = typeof extra.priceCents === 'number' ? extra.priceCents : 3_000;
  return {
    __typename: 'Offer',
    id,
    taskId: TASK,
    helperUserId: `helper-${id}`,
    helperIsTrader: false,
    helperBusinessName: null,
    helperBusinessOib: null,
    helperBusinessAddress: null,
    status: 'SENT',
    priceCents: 3_000,
    serviceFeeCents: fee(priceCents),
    serviceFeeCreditCents: 0,
    serviceFeeScheduleVersion: '1',
    serviceFeePercent: 12,
    serviceFeeMinimumCents: 250,
    totalCents: priceCents + fee(priceCents),
    message: '',
    messageMasked: null,
    expiresAt: '2026-11-10T10:00:00Z',
    isMine: false,
    createdAt: '2026-11-03T10:00:00Z',
    updatedAt: '2026-11-03T10:00:00Z',
    ...extra,
  };
}

const task = {
  __typename: 'Task',
  id: TASK,
  status: 'OPEN',
  category: 'GARDEN',
  title: 'Mow lawn for Mama',
  description: '',
  approximateLat: 42.65,
  approximateLng: 18.09,
  serviceAreaName: 'Dubrovnik',
  windowStart: '2026-11-07T08:00:00Z',
  windowEnd: '2026-11-07T12:00:00Z',
  flexibleDate: false,
  budgetCents: 3_500,
  photos: [],
  isMine: true,
  createdAt: '2026-11-01T10:00:00Z',
  publishedAt: '2026-11-01T10:00:00Z',
  address: null,
  lat: null,
  lng: null,
  accessDetails: null,
  careContact: null,
  textMasked: false,
  holdReasons: [],
};

const OFFERS = [
  offer('withdrawn', { status: 'WITHDRAWN', priceCents: 2_500 }),
  offer('marko', { priceCents: 3_000, message: 'I have my own mower.' }),
  offer('stjepan', {
    priceCents: 2_800,
    helperIsTrader: true,
    helperBusinessName: 'Obrt Stjepan Popravci',
    helperBusinessOib: '00000000698',
    helperBusinessAddress: 'Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
  }),
  offer('changed', { status: 'NEEDS_UPDATE', priceCents: 4_000 }),
];

/** A client answering TaskOffers and Task; subscriptions stay open and silent. */
function client(offers: unknown[] | Error = OFFERS) {
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        if (operation.operationName?.startsWith('Offer')) return; // a live event, never fires
        if (operation.operationName === 'Task') {
          observer.next({ data: { task } });
        } else if (offers instanceof Error) {
          observer.error(offers);
          return;
        } else {
          observer.next({ data: { taskOffers: offers } });
        }
        observer.complete();
      }),
  );
  return new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
}

async function renderScreen(
  options: {
    offers?: unknown[] | Error;
    onAccept?: (id: string) => void;
    openRanking?: (locale: string) => void;
  } = {},
) {
  await render(
    <ApolloProvider client={client(options.offers)}>
      <OffersScreen
        taskId={TASK}
        onAccept={options.onAccept ?? jest.fn()}
        openRanking={options.openRanking ?? jest.fn()}
        now={() => NOW}
      />
    </ApolloProvider>,
  );
  await screen.findByTestId('offers-content').catch(() => undefined);
}

const cardIds = () =>
  screen.getAllByTestId(/^offer-card-/).map((card) => card.props.testID as string);

describe('OffersScreen', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('shows the open offers newest first, then the withdrawn one greyed at the end', async () => {
    await renderScreen();

    expect(screen.getByText('4 offers')).toBeTruthy();
    expect(screen.getByText('Mow lawn for Mama')).toBeTruthy();
    expect(cardIds()).toEqual([
      'offer-card-marko',
      'offer-card-stjepan',
      'offer-card-changed',
      'offer-card-withdrawn',
    ]);
    const withdrawn = screen.getByTestId('offer-card-withdrawn');
    expect(within(withdrawn).getByText('Offer withdrawn')).toBeTruthy();
    expect(within(withdrawn).queryByText('Accept')).toBeNull();
  });

  it('shows the total, the helper price, trader label, new-helper state, expiry and note', async () => {
    await renderScreen();

    const marko = screen.getByTestId('offer-card-marko');
    // The all-in total the poster pays first, the helper's price second (BEAV-191).
    expect(within(marko).getByText(formatMoney(3_360, 'en'))).toBeTruthy();
    expect(within(marko).getByText('in total, incl. service fee')).toBeTruthy();
    expect(within(marko).getByText(`Helper's price ${formatMoney(3_000, 'en')}`)).toBeTruthy();
    expect(within(marko).getByText('Private individual')).toBeTruthy();
    expect(within(marko).getByText('New helper · no reviews yet')).toBeTruthy();
    expect(within(marko).getByText(/^Open until /)).toBeTruthy();
    expect(within(marko).getByText('“I have my own mower.”')).toBeTruthy();
    const stjepan = screen.getByTestId('offer-card-stjepan');
    expect(within(stjepan).getByText('Registered business')).toBeTruthy();
    // No note: no empty quote.
    expect(within(stjepan).queryByText(/“/)).toBeNull();
  });

  it.each([
    [1, 'row'],
    [3.1, 'column'],
  ])('at text scale %s puts the price %s-wise next to the helper', async (fontScale, direction) => {
    // At the largest sizes a price beside the helper's details squeezed them to nothing.
    const dimensions = jest
      .spyOn(Dimensions, 'get')
      .mockReturnValue({ width: 402, height: 874, scale: 3, fontScale });
    try {
      await renderScreen();
      const top = screen.getByTestId('offer-top-marko');
      expect(StyleSheet.flatten(top.props.style).flexDirection ?? 'column').toBe(direction);
    } finally {
      dimensions.mockRestore();
    }
  });

  it('names a trader helper’s business, OIB and registered address on its card', async () => {
    await renderScreen();

    const stjepan = screen.getByTestId('offer-card-stjepan');
    expect(within(stjepan).getByText('Obrt Stjepan Popravci · OIB 00000000698')).toBeTruthy();
    expect(
      within(stjepan).getByText(
        'Registered business address: Obala Stjepana Radića 12, 20000 Dubrovnik, HR',
      ),
    ).toBeTruthy();
    expect(within(stjepan).getByText('Contact: in-app chat')).toBeTruthy();
    expect(within(stjepan).queryByTestId('private-notice')).toBeNull();
  });

  it('says a private helper is not a trader, and never shows business details', async () => {
    await renderScreen();

    const marko = screen.getByTestId('offer-card-marko');
    expect(within(marko).getByTestId('private-notice')).toBeTruthy();
    expect(within(marko).queryByTestId('trader-identity')).toBeNull();
    expect(within(marko).queryByText(/OIB/)).toBeNull();
  });

  it('says on every open offer how obligations are split', async () => {
    await renderScreen();

    for (const id of ['marko', 'stjepan', 'changed']) {
      expect(
        within(screen.getByTestId(`offer-card-${id}`)).getByText(
          'TaskBeavo handles payment and support; the helper is responsible for doing the task.',
        ),
      ).toBeTruthy();
    }
  });

  it('links how offers are ranked, opened like the legal pages', async () => {
    const openRanking = jest.fn();
    await renderScreen({ openRanking });

    await fireEvent.press(screen.getByRole('link', { name: 'How offers are ranked' }));

    expect(openRanking).toHaveBeenCalledWith('en');
  });

  it('sorts open offers by lowest price', async () => {
    await renderScreen();

    // The group isn't focusable itself (its radios are), so find it by label.
    expect(screen.getByLabelText('Sort offers').props.accessibilityRole).toBe('radiogroup');
    const lowest = screen.getByRole('radio', { name: 'Lowest price' });
    expect(lowest.props.accessibilityState.checked).toBe(false);
    await fireEvent.press(lowest);
    expect(
      screen.getByRole('radio', { name: 'Lowest price' }).props.accessibilityState.checked,
    ).toBe(true);

    expect(cardIds()).toEqual([
      'offer-card-stjepan',
      'offer-card-marko',
      'offer-card-changed',
      'offer-card-withdrawn',
    ]);
  });

  it('accept leads to checkout with the offer id; an offer that needs an update cannot be accepted', async () => {
    const onAccept = jest.fn();
    await renderScreen({ onAccept });

    await fireEvent.press(
      within(screen.getByTestId('offer-card-marko')).getByRole('button', {
        name: `Accept the offer of ${formatMoney(3_360, 'en')}`,
      }),
    );
    expect(onAccept).toHaveBeenCalledWith('marko');

    const changed = screen.getByTestId('offer-card-changed');
    expect(within(changed).getByText(/needs to confirm this offer/)).toBeTruthy();
    const button = within(changed).getByRole('button', { name: /Accept the offer/ });
    expect(button.props.accessibilityState.disabled).toBe(true);
  });

  it('compares the open offers side by side and accepts from there', async () => {
    const onAccept = jest.fn();
    await renderScreen({ onAccept });

    await fireEvent.press(screen.getByRole('button', { name: 'Compare' }));

    const table = screen.getByTestId('offers-compare');
    expect(screen.getByRole('header', { name: 'Compare 3 offers' })).toBeTruthy();
    expect(within(table).getByText('Business')).toBeTruthy();
    expect(within(table).getAllByText('New helper')).toHaveLength(3);
    expect(
      within(table).getByLabelText(`Status for the offer of ${formatMoney(3_136, 'en')}: Business`),
    ).toBeTruthy();
    expect(
      within(table).getByLabelText(
        `Helper's price for the offer of ${formatMoney(3_136, 'en')}: ${formatMoney(2_800, 'en')}`,
      ),
    ).toBeTruthy();
    await fireEvent.press(
      within(table).getByRole('button', {
        name: `Accept the offer of ${formatMoney(3_136, 'en')}`,
      }),
    );
    expect(onAccept).toHaveBeenCalledWith('stjepan');
    await fireEvent.press(screen.getByRole('button', { name: 'Back to list' }));
    expect(screen.queryByTestId('offers-compare')).toBeNull();
  });

  it('moves an offer that expires while the screen is open to the closed ones', async () => {
    const soon = new Date(Date.now() + 300).toISOString();
    await render(
      <ApolloProvider client={client([offer('marko'), offer('soon', { expiresAt: soon })])}>
        <OffersScreen taskId={TASK} onAccept={jest.fn()} openRanking={jest.fn()} />
      </ApolloProvider>,
    );
    const card = await screen.findByTestId('offer-card-soon');
    expect(within(card).getByText('Accept')).toBeTruthy();

    await waitFor(
      () =>
        expect(
          within(screen.getByTestId('offer-card-soon')).getByText('Offer expired'),
        ).toBeTruthy(),
      { timeout: 2_000 },
    );
  });

  it('says when there are no offers yet', async () => {
    await renderScreen({ offers: [] });

    expect(await screen.findByText(catalogues.en.offers.empty)).toBeTruthy();
  });

  it('offers a retry when the offers could not load', async () => {
    await renderScreen({ offers: new Error('offline') });

    expect(await screen.findByText(catalogues.en.offers.loadError)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it.each(locales)('renders the comparison in %s', async (locale) => {
    await i18n.changeLanguage(locale);
    await renderScreen();

    const strings = catalogues[locale].offers;
    expect(screen.getByRole('button', { name: strings.compare })).toBeTruthy();
    expect(screen.getAllByText(strings.trader.private).length).toBeGreaterThan(0);
    expect(screen.getByText(strings.closed.WITHDRAWN)).toBeTruthy();
    expect(screen.getAllByText(formatMoney(3_360, locale)).length).toBeGreaterThan(0);
  });
});
