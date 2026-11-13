import { expect, test, type Page } from '@playwright/test';

/**
 * The public price list (BEAV-191, ZZP čl. 7(9)), server-rendered from the stub API in
 * e2e/legal-api-stub.mjs, at the mobile (375px) and desktop widths of playwright.config.ts.
 */

const locales = ['hr', 'en', 'de', 'fr'] as const;

/** fees.title: the footer link. */
const titles = { hr: 'Naknade', en: 'Fees', de: 'Gebühren', fr: 'Frais' } as const;

async function noHorizontalScroll(page: Page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
}

for (const locale of locales) {
  test(`/${locale}/fees renders the price list`, async ({ page }) => {
    const response = await page.goto(`/${locale}/fees`);
    expect(response?.status()).toBe(200);
    await expect(page.getByRole('heading', { level: 1 })).toContainText('2026-11');
    await expect(page.getByTestId('fees-draft')).toBeVisible();
    await expect(page.getByTestId('fees-rule')).toContainText('12');
    await expect(page.getByTestId('fees-examples')).toBeVisible();
    await expect(page.getByTestId('fees-cancellation')).toHaveAttribute(
      'href',
      `/${locale}/cancellation`,
    );
    await expect(page.getByTestId('fees-refunds')).toBeVisible();
    await expect(page.getByTestId('footer-fees')).toHaveAttribute('href', `/${locale}/fees`);
    await expect(page.getByTestId('footer-fees')).toHaveText(titles[locale]);
    expect(await noHorizontalScroll(page)).toBe(true);
  });
}

test('the price list states the fee, the examples and when it took effect', async ({ page }) => {
  await page.goto('/en/fees');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(
    'Fees — price list valid from November 1, 2026 (version 2026-11)',
  );
  await expect(page.getByTestId('fees-rule')).toContainText(
    'a service fee of 12% of the task price, minimum €2.50, per booking',
  );
  const examples = page.getByTestId('fees-examples');
  await expect(examples).toContainText('Task price €40.00 → service fee €4.80 → total €44.80.');
  await expect(examples).toContainText('Task price €15.00 → fee €2.50 (minimum) → total €17.50.');
  await expect(page.getByTestId('fees-vat')).toContainText('[Open: the accountant decides');
  await expect(page.getByTestId('fees-refund-late')).toContainText(
    '25% (min €5.00, max €20.00) goes to the helper',
  );
  await expect(page.getByTestId('fees-upcoming')).toContainText(
    'From March 1, 2027: 15% of the task price, at least €3.00.',
  );
  await expect(page.getByTestId('fees-archive')).toHaveAttribute('href', '/en/fees/archive');
});

test('every page links the price list in its footer', async ({ page }) => {
  for (const path of ['/de', '/de/imprint', '/de/terms']) {
    await page.goto(path);
    await expect(page.getByTestId('footer-fees')).toHaveAttribute('href', '/de/fees');
  }
  await page.goto('/de/imprint');
  await expect(page.getByTestId('imprint-fees').getByRole('link')).toHaveAttribute(
    'href',
    '/de/fees',
  );
});

for (const locale of locales) {
  test(`/${locale}/ranking explains the order of offers`, async ({ page }) => {
    const response = await page.goto(`/${locale}/ranking`);
    expect(response?.status()).toBe(200);
    await expect(page.getByTestId('ranking-draft')).toBeVisible();
    await expect(page.getByTestId('ranking-paid')).toBeVisible();
    await expect(page.getByTestId('footer-ranking')).toHaveAttribute('href', `/${locale}/ranking`);
    expect(await noHorizontalScroll(page)).toBe(true);
  });
}

test('the price list links the ranking of offers', async ({ page }) => {
  await page.goto('/en/fees');
  await expect(page.getByTestId('fees-ranking')).toHaveAttribute('href', '/en/ranking');
  await expect(page.getByTestId('fees-refund-rate')).toContainText(
    'the one in force when you accepted the offer',
  );
});

test('the archive keeps every earlier price list', async ({ page }) => {
  await page.goto('/en/fees/archive');
  const list = page.getByTestId('fees-archive-list');
  await expect(list).toContainText('From November 1, 2026 · in effect now');
  await expect(list).toContainText('From January 1, 2026 to October 31, 2026');
  // An announced list stays on /fees until it applies.
  await expect(list).not.toContainText('2027-03');
  await expect(list).toContainText('10% of the task price, at least €2.00.');
  expect(await noHorizontalScroll(page)).toBe(true);
});

test('the machine-readable price list is the same schedule', async ({ request }) => {
  const current = await request.get('/fees/pricelist.csv');
  expect(current.status()).toBe(200);
  expect(current.headers()['content-type']).toContain('text/csv');
  expect(await current.text()).toBe(
    'version,valid_from,valid_until,service,charged_to,basis,rate_percent,minimum_eur,currency,vat,' +
      'service_name,retail_price,special_offer,anchor_price,anchor_price_from\r\n' +
      '2026-11,2026-11-01,2027-02-28,service_fee,poster,task_price,12,2.50,EUR,TBC,' +
      'Naknada za uslugu TaskBeavo,12% (min. 2.50 EUR),no,10% (min. 2.00 EUR),2026-01-01\r\n',
  );

  const announced = await request.get('/fees/pricelist-2027-03.csv');
  expect(await announced.text()).toContain(
    '2027-03,2027-03-01,,service_fee,poster,task_price,15,3.00',
  );

  const earlier = await request.get('/fees/pricelist-2026-01.csv');
  expect(await earlier.text()).toContain('2026-01,2026-01-01,2026-10-31,service_fee');

  expect((await request.get('/fees/pricelist-1999-01.csv')).status()).toBe(404);
  expect((await request.get('/fees/other.csv')).status()).toBe(404);
});
