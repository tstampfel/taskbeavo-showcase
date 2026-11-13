import { expect, test } from '@playwright/test';

/**
 * The other specs run against a production build (playwright.config.ts). This one runs against
 * `pnpm dev`, the command developers use, and proves it serves and hydrates the home page.
 */
test('`pnpm dev` serves the home page', async ({ page }) => {
  await page.route('**/api/graphql', (route) =>
    route.fulfill({ json: { data: { apiVersion: 'dev' } } }),
  );

  await page.goto('/hr');

  await expect(page.locator('html')).toHaveAttribute('lang', 'hr');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('TaskBeavo');
  await expect(page.getByTestId('home-tagline')).toHaveText('Lokalna pomoć u Hrvatskoj');
  // Hydrated: the client component's Apollo query answered.
  await expect(page.getByTestId('api-status')).toHaveText(
    'Usluga je povezana (verzija API-ja dev)',
  );
});
