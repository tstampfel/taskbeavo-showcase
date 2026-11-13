import { expect, test } from '@playwright/test';
import { LEGAL_PAGE_KINDS, LEGAL_PAGES } from '@taskbeavo/app-links';

/**
 * The legal pages (BEAV-81), server-rendered from the stub API in e2e/legal-api-stub.mjs, at
 * the mobile (375px) and desktop widths of playwright.config.ts.
 */

const locales = ['hr', 'en', 'de', 'fr'] as const;

/** legal.documents in packages/i18n. */
const titles: Record<(typeof locales)[number], Record<string, string>> = {
  hr: {
    TERMS: 'Uvjeti korištenja',
    PRIVACY: 'Obavijest o privatnosti',
    HELPER_TERMS: 'Uvjeti za pomagače',
    CANCELLATION: 'Pravila otkazivanja i povrata novca',
    COOKIES: 'Pravila o kolačićima',
    IMPRINT: 'Podaci o tvrtki',
  },
  en: {
    TERMS: 'Terms and conditions',
    PRIVACY: 'Privacy notice',
    HELPER_TERMS: 'Helper terms',
    CANCELLATION: 'Cancellation and refund policy',
    COOKIES: 'Cookie policy',
    IMPRINT: 'Company information',
  },
  de: {
    TERMS: 'AGB',
    PRIVACY: 'Datenschutzerklärung',
    HELPER_TERMS: 'Bedingungen für Helfer',
    CANCELLATION: 'Storno- und Erstattungsbedingungen',
    COOKIES: 'Cookie-Richtlinie',
    IMPRINT: 'Impressum',
  },
  fr: {
    TERMS: 'Conditions générales',
    PRIVACY: 'Politique de confidentialité',
    HELPER_TERMS: 'Conditions pour les aidants',
    CANCELLATION: 'Conditions d’annulation et de remboursement',
    COOKIES: 'Politique relative aux cookies',
    IMPRINT: 'Mentions légales',
  },
};

for (const locale of locales) {
  for (const page of LEGAL_PAGES) {
    test(`/${locale}/${page} renders`, async ({ page: browser }) => {
      const response = await browser.goto(`/${locale}/${page}`);

      expect(response?.status()).toBe(200);
      const title = titles[locale][LEGAL_PAGE_KINDS[page]]!;
      await expect(browser.getByRole('heading', { level: 1 })).toHaveText(title);
      await expect(browser).toHaveTitle(new RegExp(`^${title.replace(/[()]/g, '\\$&')} · `));
      await expect(browser.locator('html')).toHaveAttribute('lang', locale);
      // No horizontal scroll at 375px, in any language.
      const overflow = await browser.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }
}

test('shows the version in force with its date, text and every other version', async ({ page }) => {
  await page.goto('/de/terms');

  await expect(page.getByTestId('legal-meta')).toContainText('Version 2026-11');
  await expect(page.getByTestId('legal-meta')).toContainText('Gültig ab');
  await expect(page.getByTestId('legal-body')).toHaveAttribute('lang', 'de');
  await expect(page.getByTestId('legal-body').getByRole('heading', { level: 2 })).toHaveText(
    '1. TERMS 2026-11 (DE)',
  );
  await expect(page.getByTestId('legal-draft')).toHaveCount(0);

  await page.getByTestId('legal-versions').getByRole('link').click();
  await expect(page).toHaveURL(/\/de\/terms\/2027-01$/);
  await expect(page.getByTestId('legal-not-current')).toContainText(
    'Noch nicht in Kraft – gilt ab',
  );
  await expect(page.getByTestId('legal-not-current')).toContainText('in Kraft ist Version 2026-11');
  await page.getByTestId('legal-not-current').getByRole('link').click();
  await expect(page).toHaveURL(/\/de\/terms$/);
});

test('an unknown version is not found', async ({ page }) => {
  const response = await page.goto('/en/terms/1999-01');
  expect(response?.status()).toBe(404);
});

test('marks a draft and keeps it out of search results', async ({ page }) => {
  await page.goto('/fr/privacy');

  await expect(page.getByTestId('legal-draft')).toHaveText(
    `Brouillon pour les tests. Pas pour de vrais utilisateurs ni de vrais paiements tant que la version approuvée par l’avocat n’est pas publiée.`,
  );
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/);
});

test('the version in force is indexable, with hreflang, canonical and breadcrumbs', async ({
  page,
}) => {
  await page.goto('/en/helper-terms');

  await expect(page.locator('meta[name="robots"]')).toHaveCount(0);
  await expect(page.locator('meta[name="description"]')).toHaveAttribute(
    'content',
    /^Helper terms of TaskBeavo/,
  );
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
    'href',
    /\/en\/helper-terms$/,
  );
  for (const locale of locales) {
    await expect(page.locator(`link[rel="alternate"][hreflang="${locale}"]`)).toHaveAttribute(
      'href',
      new RegExp(`/${locale}/helper-terms$`),
    );
  }
  const jsonLd = JSON.parse(
    (await page.locator('script[type="application/ld+json"]').textContent()) ?? '{}',
  );
  expect(jsonLd['@type']).toBe('BreadcrumbList');
});

test('says when a document is not published yet', async ({ page }) => {
  await page.goto('/hr/cookies');
  await expect(page.getByTestId('legal-unavailable')).toContainText('Još nije objavljeno');
});

test("the imprint carries MaxMind's attribution while GeoLite2 is in use", async ({ page }) => {
  await page.goto('/de/imprint');
  await expect(page.getByTestId('legal-geolite')).toHaveText(
    'This product includes GeoLite Data created by MaxMind, available from https://www.maxmind.com. Dieses Produkt enthält GeoLite-Daten, die von MaxMind erstellt wurden und unter https://www.maxmind.com verfügbar sind.',
  );
  // Also in the privacy notice, which describes the new-sign-in email.
  await page.goto('/de/privacy');
  await expect(page.getByTestId('legal-geolite')).toContainText('GeoLite Data created by MaxMind');
  await page.goto('/de/terms');
  await expect(page.getByTestId('legal-geolite')).toHaveCount(0);
});

for (const locale of locales) {
  test(`every footer link opens its legal page in ${locale}`, async ({ page }) => {
    await page.goto(`/${locale}`);
    const links = page.getByTestId('legal-footer').getByRole('link');
    // The six legal pages, then the price list and the offer ranking (BEAV-191).
    await expect(links).toHaveCount(LEGAL_PAGES.length + 2);

    for (const href of await links.evaluateAll((all) =>
      all.map((link) => link.getAttribute('href')),
    )) {
      const response = await page.request.get(href!);
      expect(response.status(), href!).toBe(200);
    }
  });
}

/**
 * The links other screens build to a legal page (sign-up, checkout, terms update, the app's
 * in-app browser) all come from legalPath/legalUrl; this checks the paths they produce render.
 */
test('every linked legal URL renders in hr, en, de and fr', async ({ request }) => {
  const linked = ['terms', 'privacy', 'cancellation'] as const;
  for (const locale of locales) {
    for (const page of linked) {
      const response = await request.get(`/${locale}/${page}`);
      expect(response.status(), `/${locale}/${page}`).toBe(200);
    }
  }
});
