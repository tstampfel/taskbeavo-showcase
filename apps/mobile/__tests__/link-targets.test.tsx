import { catalogues, locales } from '@taskbeavo/i18n';
import { fireEvent, renderRouter, screen } from 'expo-router/testing-library';
import { Text } from 'react-native';
import NotFound from '../app/+not-found';
import NoAccess from '../app/bookings/[id]/no-access';
import ReleasePayment from '../app/bookings/[id]/release';
import Conversation from '../app/conversations/[id]';
import Payouts from '../app/helper/payouts/index';
import PayoutsRefresh from '../app/helper/payouts/refresh';
import PayoutsReturn from '../app/helper/payouts/return';
import TaskDetail from '../app/tasks/[id]/index';
import Checkout from '../app/tasks/[id]/offers/[offerId]/checkout';
import { i18n } from '../src/i18n';
import { appPathFromLink } from '../src/links/routes';

const routes = {
  index: () => <Text testID="home">home</Text>,
  'tasks/[id]/index': TaskDetail,
  'tasks/[id]/offers/[offerId]/checkout': Checkout,
  'bookings/[id]/release': ReleasePayment,
  'bookings/[id]/no-access': NoAccess,
  'conversations/[id]': Conversation,
  'helper/payouts/index': Payouts,
  'helper/payouts/return': PayoutsReturn,
  'helper/payouts/refresh': PayoutsRefresh,
  '+not-found': NotFound,
};

describe('screens opened by links', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it.each([
    ['https://taskbeavo.com/de/tasks/42', '/tasks/42', 'task'],
    ['https://taskbeavo.com/fr/bookings/7/release', '/bookings/7/release', 'release'],
    ['taskbeavo://bookings/7/no-access', '/bookings/7/no-access', 'noAccess'],
    ['/conversations/3', '/conversations/3', 'conversation'],
    ['/helper/payouts', '/helper/payouts', 'payouts'],
    ['https://taskbeavo.com/helper/payouts/return', '/helper/payouts/return', 'payouts'],
    ['taskbeavo://helper/payouts/refresh', '/helper/payouts/refresh', 'payouts'],
  ] as const)('%s opens %s', async (link, pathname, target) => {
    await i18n.changeLanguage('en');
    await renderRouter(routes, { initialUrl: appPathFromLink(link) });
    expect(screen).toHavePathname(pathname);
    expect(screen.getByTestId(`link-target-${target}`)).toBeOnTheScreen();
    expect(screen.getByRole('header')).toHaveTextContent(catalogues.en.linkTarget[target]);
  });

  it.each(locales)(
    'checkout says in %s that card payments are not set up in a build without a Stripe key',
    async (locale) => {
      await i18n.changeLanguage(locale);
      await renderRouter(routes, { initialUrl: appPathFromLink('/tasks/42/offers/7/checkout') });
      const messages = catalogues[locale].checkout;
      expect(screen).toHavePathname('/tasks/42/offers/7/checkout');
      expect(screen.getByRole('header')).toHaveTextContent(messages.title);
      expect(screen.getByText(messages.notConfigured)).toBeOnTheScreen();
      // Nothing that books or pays.
      expect(screen.queryAllByRole('button')).toHaveLength(0);
    },
  );

  it.each(locales)(
    'the release screen explains in %s that the link released nothing',
    async (locale) => {
      await i18n.changeLanguage(locale);
      await renderRouter(routes, { initialUrl: '/bookings/7/release' });
      const messages = catalogues[locale].linkTarget;
      expect(screen.getByRole('header')).toHaveTextContent(messages.release);
      expect(screen.getByText(messages.safety)).toBeOnTheScreen();
      // A placeholder: the only action is going home, nothing that moves money.
      expect(screen.getAllByRole('button')).toHaveLength(1);
      expect(screen.getByRole('button')).toHaveTextContent(catalogues[locale].notFound.backHome);
    },
  );

  it('goes back to the home screen with a tap', async () => {
    await i18n.changeLanguage('en');
    await renderRouter(routes, { initialUrl: '/bookings/7/no-access' });
    await fireEvent.press(screen.getByRole('button'));
    expect(screen).toHavePathname('/');
    expect(screen.getByTestId('home')).toBeOnTheScreen();
  });

  it('lands an unknown link on the home screen', async () => {
    await renderRouter(routes, { initialUrl: appPathFromLink('https://taskbeavo.com/admin') });
    expect(screen).toHavePathname('/');
  });

  it('shows a translated not-found screen for an unknown in-app route', async () => {
    await i18n.changeLanguage('de');
    await renderRouter(routes, { initialUrl: '/nowhere' });
    expect(screen.getByTestId('not-found')).toBeOnTheScreen();
    expect(screen.getByRole('header')).toHaveTextContent(catalogues.de.notFound.title);
  });
});
