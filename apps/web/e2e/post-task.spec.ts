import type { Page, Route } from '@playwright/test';
import { expect, test, type Answers, type Variables } from './graphql-stub';

/**
 * Post a task on the web (mockup 03, BEAV-87) against a stubbed API and a stubbed Nominatim, at
 * the mobile (375px) and desktop widths of playwright.config.ts. postTask answers task 42, which
 * the server-side stub (e2e/legal-api-stub.mjs) renders as the public task page.
 */

const viewer = {
  __typename: 'User',
  id: 'poster-1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Primjer',
  locale: 'en',
  country: 'AT',
  timeZone: 'Europe/Vienna',
  phoneVerified: true,
  phoneNumberMasked: null,
};

const listing = {
  __typename: 'TaskListing',
  id: '42',
  category: 'GARDEN',
  title: 'Mow the lawn and trim the hedge',
  description: 'Mow the lawn and trim the hedge.',
  approximateLat: 42.66,
  approximateLng: 18.09,
  serviceAreaName: 'Dubrovnik',
  windowStart: null,
  windowEnd: null,
  flexibleDate: true,
  budgetCents: 3_000,
  photos: [],
  isMine: true,
  publishedAt: '2026-11-20T08:00:00Z',
};

function task(input: Record<string, unknown>) {
  return {
    __typename: 'Task',
    id: '42',
    status: 'OPEN',
    category: input.category,
    title: input.title,
    description: input.description,
    approximateLat: 42.66,
    approximateLng: 18.09,
    serviceAreaName: 'Dubrovnik',
    windowStart: input.windowStart,
    windowEnd: input.windowEnd,
    flexibleDate: input.flexibleDate,
    budgetCents: input.budgetCents,
    photos: [],
    isMine: true,
    createdAt: '2026-11-20T08:00:00Z',
    publishedAt: '2026-11-20T08:00:00Z',
    address: 'Ulica Ante Starčevića 1, Dubrovnik',
    lat: 42.6583,
    lng: 18.0861,
    accessDetails: input.accessDetails ?? null,
    onSiteContact: null,
    careContact: null,
    textMasked: false,
    holdReasons: [],
  };
}

const answers = (extra: Answers = {}, signedIn = true): Answers => ({
  Viewer: () => ({ data: { viewer: signedIn ? viewer : null } }),
  TermsStatus: () => ({
    data: {
      viewer: signedIn
        ? { __typename: 'User', id: viewer.id, mustAcceptTerms: false, termsToAccept: null }
        : null,
    },
  }),
  ApiVersion: () => ({ data: { apiVersion: 'test' } }),
  MyCareContacts: () => ({ data: { myCareContacts: [] } }),
  TaskListing: () => ({ data: { taskListing: listing } }),
  ...extra,
});

const NOMINATIM = 'https://nominatim.openstreetmap.org/**';

/** Nominatim answers one real place in Dubrovnik, and records each request. */
async function stubNominatim(page: Page, fail = false) {
  const requests: { url: string; referer: string | undefined }[] = [];
  await page.route(NOMINATIM, async (route: Route) => {
    requests.push({ url: route.request().url(), referer: route.request().headers()['referer'] });
    if (fail) {
      await route.fulfill({ status: 503, body: 'unavailable' });
      return;
    }
    await route.fulfill({
      headers: { 'access-control-allow-origin': '*' },
      json: [
        {
          place_id: 7,
          lat: '42.6583',
          lon: '18.0861',
          display_name: 'Ulica Ante Starčevića 1, Dubrovnik, Hrvatska',
          address: { town: 'Dubrovnik', country_code: 'hr' },
        },
      ],
    });
  });
  return requests;
}

async function storedDraft(page: Page) {
  return page.evaluate(() => ({
    local: Object.entries(localStorage)
      .filter(([key]) => key.startsWith('taskbeavo.task'))
      .map(([, value]) => value)
      .join(''),
    session: Object.entries(sessionStorage)
      .filter(([key]) => key.startsWith('taskbeavo.task'))
      .map(([, value]) => value)
      .join(''),
  }));
}

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

/** Steps 1 to 3 with valid answers; leaves the wizard on "When". */
async function fillToWhen(page: Page) {
  await page.getByTestId('post-category-ERRANDS').check();
  await page.getByTestId('post-title').fill('Grocery run for my mum');
  await page.getByTestId('post-next').click();
  await page.getByTestId('post-description').fill('Bread, milk and fruit from Gruž market.');
  await page.getByTestId('post-next').click();
  await page.getByTestId('post-address').fill('Starčevića 1, Dubrovnik');
  await page.getByTestId('post-address-search').click();
  await page.getByTestId('post-address-result-0').click();
  await expect(page.getByTestId('post-address-value')).toHaveText(
    'Ulica Ante Starčevića 1, Dubrovnik, Hrvatska',
  );
  await page.getByTestId('post-access').fill('Gate code 4417');
  await page.getByTestId('post-next').click();
}

const headings = {
  hr: 'U čemu Vam treba pomoć?',
  en: 'What do you need help with?',
  de: 'Wobei brauchen Sie Hilfe?',
  fr: "Pour quoi avez-vous besoin d'aide ?",
} as const;

for (const locale of ['hr', 'en', 'de', 'fr'] as const) {
  test(`posts a task end to end in ${locale}`, async ({ page, stubGraphql }) => {
    const posted: Variables[] = [];
    await stubGraphql(
      answers({
        PostTask: (variables) => {
          posted.push(variables);
          const input = variables.input as Record<string, unknown>;
          return {
            data: {
              postTask: { __typename: 'TaskPayload', id: '42', task: task(input), warnings: [] },
            },
          };
        },
      }),
    );
    const nominatim = await stubNominatim(page);

    // Home's "Post a task" opens the wizard.
    await page.goto(`/${locale}`);
    await page.getByTestId('post-task-link').click();
    await expect(page).toHaveURL(new RegExp(`/${locale}/tasks/new$`));
    await expect(page.getByTestId('post-step-heading')).toHaveText(headings[locale]);
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', /noindex/);
    await noHorizontalScroll(page);

    // Next stops on an incomplete step and says why.
    await page.getByTestId('post-next').click();
    await expect(page.getByTestId('post-category-error')).toBeVisible();

    await fillToWhen(page);
    // The address went to Nominatim, Croatia only, in the page language, with nothing
    // personal: the Referer is the site's origin alone.
    expect(nominatim).toHaveLength(1);
    expect(nominatim[0]!.url).toContain('countrycodes=hr');
    expect(nominatim[0]!.url).toContain(`accept-language=${locale}`);
    expect(nominatim[0]!.referer ?? '').not.toContain('/tasks/new');

    // The access details are never stored in the browser (hard rule e).
    const stored = await storedDraft(page);
    expect(stored.local + stored.session).not.toContain('4417');
    expect(stored.local).toContain('Grocery run for my mum');
    expect(stored.local).not.toContain('Starčevića');

    await page.getByTestId('post-when-flexible').check();
    await page.getByTestId('post-next').click();

    // Rule k: the budget starts empty, and the poster types it.
    const budget = page.getByTestId('post-budget');
    await expect(budget).toHaveValue('');
    await budget.fill('30');
    await page.getByTestId('post-next').click();

    await expect(page.getByTestId('post-review')).toBeVisible();
    await expect(page.getByTestId('post-review-what')).toHaveText('Grocery run for my mum');
    await expect(page.getByTestId('post-review-budget')).toContainText('30');
    await noHorizontalScroll(page);
    await page.getByTestId('post-submit').click();

    // The new task opens, with the poster's "See offers".
    await expect(page).toHaveURL(new RegExp(`/${locale}/tasks/42$`));
    await expect(page.getByTestId('see-offers')).toBeVisible();
    expect(posted).toHaveLength(1);
    expect(posted[0]!.input).toMatchObject({
      category: 'ERRANDS',
      title: 'Grocery run for my mum',
      description: 'Bread, milk and fruit from Gruž market.',
      location: {
        address: 'Ulica Ante Starčevića 1, Dubrovnik, Hrvatska',
        lat: 42.6583,
        lng: 18.0861,
      },
      accessDetails: 'Gate code 4417',
      flexibleDate: true,
      windowStart: null,
      windowEnd: null,
      budgetCents: 3_000,
      photoIds: [],
    });

    // Posting deletes the draft.
    expect(await storedDraft(page)).toEqual({ local: '', session: '' });
  });
}

test('an address outside the service area offers the waitlist', async ({ page, stubGraphql }) => {
  const joined: Variables[] = [];
  await stubGraphql(
    answers({
      PostTask: () => ({
        data: null,
        errors: [{ message: 'Outside.', extensions: { code: 'OUT_OF_SERVICE_AREA' } }],
      }),
      JoinWaitlist: (variables) => {
        joined.push(variables);
        return {
          data: {
            joinWaitlist: {
              __typename: 'WaitlistEntry',
              id: 'w-1',
              city: 'Dubrovnik',
              countryCode: 'HR',
            },
          },
        };
      },
    }),
  );
  await stubNominatim(page);
  await page.goto('/en/tasks/new');
  await fillToWhen(page);
  await page.getByTestId('post-when-flexible').check();
  await page.getByTestId('post-next').click();
  await page.getByTestId('post-budget').fill('25');
  await page.getByTestId('post-next').click();
  await page.getByTestId('post-submit').click();

  const card = page.getByTestId('post-waitlist');
  await expect(card).toBeVisible();
  await expect(page.getByTestId('post-submit')).toHaveCount(0);
  await expect(page.getByTestId('post-waitlist-city')).toHaveValue('Dubrovnik');
  await expect(page.getByTestId('post-waitlist-email')).toHaveValue('ana@example.com');
  const join = page.getByTestId('post-waitlist-join');
  await expect(join).toBeDisabled();
  await page.getByTestId('post-waitlist-consent').check();
  await join.click();
  await expect(page.getByTestId('post-waitlist-joined')).toBeVisible();
  expect(joined[0]!.input).toMatchObject({
    email: 'ana@example.com',
    city: 'Dubrovnik',
    countryCode: 'HR',
    category: 'ERRANDS',
    locale: 'EN',
    consentLaunchEmail: true,
  });

  // Changing the address goes back to "Where", and Next returns to the review.
  await page.getByTestId('post-waitlist-change-address').click();
  await expect(page.getByTestId('post-address-picked')).toBeVisible();
  await expect(page.getByTestId('post-next')).toHaveText('Back to review');
});

test('a draft resumes after a reload, and Start again clears it', async ({ page, stubGraphql }) => {
  await stubGraphql(answers());
  await page.goto('/en/tasks/new');
  await page.getByTestId('post-category-GARDEN').check();
  await page.getByTestId('post-title').fill('Mow the lawn');
  await page.getByTestId('post-next').click();
  await expect(page.getByTestId('post-description')).toBeVisible();

  await page.reload();
  await expect(page.getByTestId('post-resume')).toContainText('Mow the lawn');
  await page.getByTestId('post-resume-continue').click();
  await expect(page.getByTestId('post-description')).toBeVisible();
  await page.getByTestId('post-back').click();
  await expect(page.getByTestId('post-title')).toHaveValue('Mow the lawn');

  await page.reload();
  await page.getByTestId('post-resume-start-again').click();
  await expect(page.getByTestId('post-title')).toHaveValue('');
  await expect(page.getByTestId('post-category-GARDEN')).not.toBeChecked();
});

test('an unreachable address search keeps the draft and offers a retry', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers());
  await stubNominatim(page, true);
  await page.goto('/en/tasks/new');
  await page.getByTestId('post-category-REPAIRS').check();
  await page.getByTestId('post-title').fill('Fix a shelf');
  await page.getByTestId('post-next').click();
  await page.getByTestId('post-description').fill('One shelf in the kitchen.');
  await page.getByTestId('post-next').click();
  await page.getByTestId('post-address').fill('Lapad, Dubrovnik');
  await page.getByTestId('post-address-search').click();

  const failed = page.getByTestId('post-address-failed');
  await expect(failed).toBeVisible();
  await expect(failed.getByRole('button', { name: 'Try again' })).toBeVisible();
  expect((await storedDraft(page)).local).toContain('Fix a shelf');
  // Never a made-up place: Next still asks for a picked address.
  await page.getByTestId('post-next').click();
  await expect(page.getByText('Search for the address and pick it from the list.')).toBeVisible();
});

test('signed out, the wizard asks to sign in first', async ({ page, stubGraphql }) => {
  await stubGraphql(answers({}, false));
  await page.goto('/de/tasks/new');
  await expect(page.getByTestId('post-sign-in')).toContainText(
    'Melden Sie sich an, um eine Aufgabe zu posten.',
  );
  await expect(page.getByTestId('post-sign-in-link')).toHaveAttribute(
    'href',
    /\/de\/sign-in\?next=%2Ftasks%2Fnew$/,
  );
  await expect(page.getByTestId('post-task')).toHaveCount(0);
});

test('signing out deletes the draft', async ({ page, stubGraphql }) => {
  let signedIn = true;
  await stubGraphql({
    ...answers(),
    Viewer: () => ({ data: { viewer: signedIn ? viewer : null } }),
    TermsStatus: () => ({
      data: {
        viewer: signedIn
          ? { __typename: 'User', id: viewer.id, mustAcceptTerms: false, termsToAccept: null }
          : null,
      },
    }),
  });
  await page.route('**/api/auth/sign-out', async (route) => {
    signedIn = false;
    await route.fulfill({ json: { data: null } });
  });
  await page.goto('/en/tasks/new');
  await page.getByTestId('post-category-GARDEN').check();
  await page.getByTestId('post-title').fill('Mow the lawn');
  await expect.poll(async () => (await storedDraft(page)).local).toContain('Mow the lawn');

  await page.getByTestId('account-menu-button').click();
  await page.getByTestId('sign-out').click();
  await expect(page).toHaveURL(/\/en$/);
  expect(await storedDraft(page)).toEqual({ local: '', session: '' });
});

test('fits 375px in German and French, with 44px targets', async ({ page, stubGraphql }) => {
  test.skip(page.viewportSize()!.width !== 375, 'the 375px layout check');
  await stubGraphql(answers());
  await stubNominatim(page);
  for (const locale of ['de', 'fr']) {
    await page.goto(`/${locale}/tasks/new`);
    await page.evaluate(() => {
      localStorage.clear();
      sessionStorage.clear();
    });
    await page.reload();
    await expect(page.getByTestId('post-task')).toBeVisible();
    await noHorizontalScroll(page);
    for (const id of ['post-next', 'app-back']) {
      const box = (await page.getByTestId(id).boundingBox())!;
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    const chip = (await page.getByTestId('post-category-CHECK_IN').locator('..').boundingBox())!;
    expect(chip.height).toBeGreaterThanOrEqual(44);
    expect(chip.x + chip.width).toBeLessThanOrEqual(375);

    // The calendar's seven days fit, each a 44px target.
    await fillToWhen(page);
    await page.getByTestId('post-when-pick').check();
    await expect(page.getByTestId('post-calendar')).toBeVisible();
    await noHorizontalScroll(page);
    const day = (await page
      .locator('[data-testid^="post-day-"]:not([disabled])')
      .first()
      .boundingBox())!;
    expect(day.width).toBeGreaterThanOrEqual(44);
    expect(day.height).toBeGreaterThanOrEqual(44);
  }
});

test('a tab never takes another draft’s address after it is cleared elsewhere (R1-1)', async ({
  page,
  context,
  stubGraphql,
}) => {
  await stubGraphql(answers());
  await stubNominatim(page);
  await page.goto('/en/tasks/new');
  await fillToWhen(page);
  await expect.poll(async () => (await storedDraft(page)).session).toContain('Starčevića');

  // A second tab (same browser storage, its own session) starts again with a new draft. The
  // fixture stubs the first page only, so this one gets the same answers by hand.
  const other = await context.newPage();
  await other.route('**/api/graphql', async (route) => {
    const body = route.request().postDataJSON() as { operationName?: string };
    const answer = answers()[body.operationName ?? ''];
    await route.fulfill({ json: answer ? answer({}) : { data: null } });
  });
  await other.goto('/en/tasks/new');
  await other.getByTestId('post-resume-start-again').click();
  await other.getByTestId('post-category-GARDEN').check();
  await other.getByTestId('post-title').fill('Wash the car');
  await expect.poll(async () => (await storedDraft(other)).local).toContain('Wash the car');

  // The first tab reloads: it resumes the new draft, without its old address.
  await page.reload();
  await expect(page.getByTestId('post-resume')).toContainText('Wash the car');
  await page.getByTestId('post-resume-continue').click();
  await page.getByTestId('post-next').click();
  await page.getByTestId('post-description').fill('Inside and out.');
  await page.getByTestId('post-next').click();
  await expect(page.getByTestId('post-address')).toBeVisible();
  await expect(page.getByTestId('post-address-picked')).toHaveCount(0);
  expect((await storedDraft(page)).session).not.toContain('Starčevića');
});

test('the API being down is not "sign in" (R1-4)', async ({ page, stubGraphql }) => {
  let down = true;
  await stubGraphql(
    answers({
      Viewer: () =>
        down
          ? { data: null, errors: [{ message: 'Down.', extensions: { code: 'INTERNAL' } }] }
          : { data: { viewer } },
    }),
  );
  await page.goto('/en/tasks/new');
  await expect(page.getByTestId('post-load-error')).toBeVisible();
  await expect(page.getByTestId('post-sign-in')).toHaveCount(0);
  down = false;
  await page.getByTestId('post-load-retry').click();
  await expect(page.getByTestId('post-task')).toBeVisible();
});

test('the address search needs three characters, and credits OpenStreetMap in the page language', async ({
  page,
  stubGraphql,
}) => {
  await stubGraphql(answers());
  const nominatim = await stubNominatim(page);
  await page.goto('/de/tasks/new');
  await page.getByTestId('post-category-GARDEN').check();
  await page.getByTestId('post-title').fill('Rasen mähen');
  await page.getByTestId('post-next').click();
  await page.getByTestId('post-description').fill('Vorne und hinten.');
  await page.getByTestId('post-next').click();

  await page.getByTestId('post-address').fill('Gr');
  await expect(page.getByTestId('post-address-search')).toBeDisabled();
  await page.getByTestId('post-address').fill('Gruž');
  await page.getByTestId('post-address-search').click();
  await expect(page.getByTestId('post-address-attribution')).toHaveText(
    '© OpenStreetMap-Mitwirkende',
  );
  expect(nominatim).toHaveLength(1);
});
