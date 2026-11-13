import { ApolloClient, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { MockLink } from '@apollo/client/testing';
import { ViewerDocument, type ViewerQuery } from '@taskbeavo/graphql';
import { TASK_TIME_ZONE, catalogues, languageNames, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { AccountMenu } from '../account-menu';
import { AppShell } from './app-shell';
import { SiteHeader } from './site-header';

const route = vi.hoisted(() => ({ pathname: '/bookings/7/cancel' }));

vi.mock('../../i18n/navigation', () => ({
  Link: ({
    href,
    locale,
    children,
    ...props
  }: {
    href: string | { pathname: string; query?: Record<string, string> };
    locale?: string;
    children: ReactNode;
  }) => {
    const path = typeof href === 'string' ? href : href.pathname;
    const query = typeof href === 'string' ? '' : new URLSearchParams(href.query).toString();
    return (
      <a href={`/${locale ?? 'xx'}${path}${query ? `?${query}` : ''}`} {...props}>
        {children}
      </a>
    );
  },
  usePathname: () => route.pathname,
  useRouter: () => ({ replace: () => undefined, push: () => undefined }),
}));

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(),
}));

type Viewer = NonNullable<ViewerQuery['viewer']>;

function client(viewer: Viewer | null) {
  const cache = new InMemoryCache();
  // The cache stores the typename the client adds to every selection.
  const data = { viewer: viewer && { __typename: 'User', ...viewer } } as ViewerQuery;
  cache.writeQuery({ query: ViewerDocument, data });
  return new ApolloClient({ cache, link: new MockLink([]) });
}

function html(locale: Locale, node: ReactNode, viewer: Viewer | null = null) {
  return renderToStaticMarkup(
    <ApolloProvider client={client(viewer)}>
      <NextIntlClientProvider
        locale={locale}
        messages={catalogues[locale]}
        timeZone={TASK_TIME_ZONE}
      >
        {node}
      </NextIntlClientProvider>
    </ApolloProvider>,
  );
}

/** The opening tag of the element carrying `testId`. */
function tag(markup: string, testId: string): string | undefined {
  return new RegExp(`<[a-z0-9]+[^>]*data-testid="${testId}"[^>]*>`).exec(markup)?.[0];
}

/** Everything inside the account menu panel. */
function panel(markup: string): string {
  const start = markup.indexOf('data-testid="account-menu"');
  expect(start).toBeGreaterThan(-1);
  return markup.slice(start);
}

const ana: Viewer = {
  id: '1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Example',
  locale: 'DE',
  country: 'DE',
  timeZone: 'Europe/Berlin',
  phoneVerified: true,
  phoneNumberMasked: '+49 ••• 789',
};

describe('AppShell top bar', () => {
  for (const locale of locales) {
    it(`shows back, the title as the page h1 and the account menu in ${locale}`, () => {
      const t = catalogues[locale].appShell;
      const markup = html(
        locale,
        <AppShell title="Cancel booking" back="/bookings/7">
          <main>body</main>
        </AppShell>,
      );

      expect(tag(markup, 'app-back')).toContain(`aria-label="${t.back}"`);
      expect(tag(markup, 'app-back')).toContain('href="/xx/bookings/7"');
      expect(markup).toMatch(/<h1[^>]*data-testid="app-title"[^>]*>Cancel booking<\/h1>/);
      const button = tag(markup, 'account-menu-button');
      expect(button).toContain(`aria-label="${t.accountMenu}"`);
      expect(button).toContain('aria-expanded="false"');
      // The bar comes first, then the page.
      expect(markup.indexOf('app-top-bar')).toBeLessThan(markup.indexOf('<main>'));
    });
  }

  it('leaves out back on a page without a parent', () => {
    const markup = html('en', <AppShell title="Booking">body</AppShell>);
    expect(tag(markup, 'app-back')).toBeUndefined();
    expect(tag(markup, 'app-title')).toBeDefined();
  });

  it('renders the title as plain text while the page body keeps its own h1', () => {
    const markup = html(
      'en',
      <AppShell title="Settings" titleIsHeading={false}>
        body
      </AppShell>,
    );
    expect(markup).not.toContain('<h1');
    expect(markup).toMatch(/<p[^>]*data-testid="app-title"[^>]*>Settings<\/p>/);
  });

  it('puts page actions before the account menu', () => {
    const markup = html(
      'en',
      <AppShell title="Fix dripping tap" actions={<button data-testid="more">More</button>}>
        body
      </AppShell>,
    );
    expect(markup.indexOf('data-testid="more"')).toBeLessThan(
      markup.indexOf('data-testid="account-menu-button"'),
    );
  });

  it('holds every account control and the language switcher, closed until tapped', () => {
    const t = catalogues.de;
    const markup = html('de', <AppShell title="Buchung">body</AppShell>, ana);
    const menu = panel(markup);

    expect(tag(markup, 'account-menu')).toContain('hidden');
    expect(menu).toContain(t.signIn.signedInAs.replace('{name}', 'Ana'));
    expect(menu).toContain('data-testid="settings-link"');
    expect(menu).toContain(`>${t.signIn.signOut}<`);
    expect(menu).toContain(`>${t.sessions.signOutEverywhere}<`);
    expect(menu).toContain('data-testid="language-switcher"');
    for (const locale of locales) expect(menu).toContain(`aria-label="${languageNames[locale]}"`);
  });

  it('offers "Log in" and the language switcher in the menu while signed out', () => {
    const markup = html('en', <AppShell title="Booking">body</AppShell>);
    const menu = panel(markup);
    expect(menu).toContain('data-testid="sign-in-link"');
    expect(menu).toContain('href="/xx/sign-in?next=%2Fbookings%2F7%2Fcancel"');
    expect(menu).toContain('data-testid="language-switcher"');
    expect(menu).not.toContain('data-testid="sign-out"');
  });
});

describe('SiteHeader', () => {
  it('shows the language switcher and "Log in" while signed out, with no menu', () => {
    const markup = html('en', <SiteHeader />);
    expect(markup).toContain('data-testid="language-switcher"');
    expect(markup).toContain('data-testid="sign-in-link"');
    expect(markup).not.toContain('data-testid="account-menu-button"');
  });

  it('puts the session controls in the menu once signed in, not in the header strip', () => {
    const markup = html('en', <SiteHeader />, ana);
    const button = markup.indexOf('data-testid="account-menu-button"');
    expect(button).toBeGreaterThan(-1);
    // Nothing of the session shows before the menu button; it all sits in the closed panel.
    expect(markup.slice(0, button)).not.toContain('signed-in-as');
    expect(tag(markup, 'account-menu')).toContain('hidden');
    const menu = panel(markup);
    expect(menu).toContain('data-testid="sign-out"');
    expect(menu).toContain('data-testid="sign-out-everywhere"');
    // Public pages show the switcher beside the menu, so the menu doesn't repeat it.
    expect(menu).not.toContain('data-testid="language-switcher"');
  });
});

describe('AccountMenu', () => {
  it('shows nothing on the sign-in page while signed out', () => {
    route.pathname = '/sign-in';
    try {
      expect(html('en', <AccountMenu />)).toBe('');
    } finally {
      route.pathname = '/bookings/7/cancel';
    }
  });
});
