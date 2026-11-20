import { emptyDraft, type TaskDraft } from '@taskbeavo/graphql';
import { TASK_TIME_ZONE, catalogues, formatMoney, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { excerpt, reviewSections } from './review-step';
import { BudgetStep, WhatStep, WhenStep } from './steps';

vi.mock('../../uploads/photo-uploader', () => ({ PhotoUploader: () => null }));

// Thursday 19 November 2026, 10:00 in Zagreb: the morning window has started, the afternoon not.
const NOW = new Date('2026-11-19T09:00:00Z');

function render(locale: Locale, element: ReactElement) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      {element}
    </NextIntlClientProvider>,
  )
    .replaceAll('&#x27;', "'")
    .replaceAll('&quot;', '"');
}

const props = (draft: TaskDraft, problems: string[] = []) => ({
  draft,
  change: () => undefined,
  problems,
  headingRef: null,
});

/** catalogues[locale] looked up by a dotted key, as next-intl does. */
function message(locale: Locale, key: string): string {
  return key
    .split('.')
    .reduce<unknown>(
      (node, part) => (node as Record<string, unknown>)[part],
      catalogues[locale],
    ) as string;
}

describe('post-task steps (BEAV-87)', () => {
  it.each(locales)('offers the five categories and the title in %s', (locale) => {
    const html = render(locale, <WhatStep {...props(emptyDraft())} />);
    expect(html).toContain(message(locale, 'postTask.what.heading'));
    for (const category of ['GARDEN', 'ERRANDS', 'REPAIRS', 'CLEANING', 'CHECK_IN']) {
      expect(html).toContain(`data-testid="post-category-${category}"`);
      expect(html).toContain(message(locale, `enums.TaskCategory.${category}`));
    }
  });

  it.each(locales)('shows the step problems once the poster tried to go on, in %s', (locale) => {
    const html = render(
      locale,
      <WhatStep {...props(emptyDraft(), ['postTask.errors.category', 'postTask.errors.title'])} />,
    );
    expect(html).toContain(message(locale, 'postTask.errors.category'));
    expect(html).toContain(message(locale, 'postTask.errors.title'));
    expect(html).toContain('aria-invalid="true"');
  });

  it.each(locales)(
    'leaves the budget to the poster: empty, no amount anywhere, in %s (rule k)',
    (locale) => {
      const html = render(locale, <BudgetStep {...props(emptyDraft())} />);
      expect(html).toContain(message(locale, 'postTask.budget.label'));
      expect(html).toMatch(
        /data-testid="post-budget"[^>]*value=""|value=""[^>]*data-testid="post-budget"/,
      );
      expect(html).not.toContain('placeholder');
      // No euro amount: no digits next to a euro sign, in either order.
      expect(html).not.toMatch(/\d\s?€|€\s?\d/);
    },
  );

  it('shows the minimum-budget error with the API message', () => {
    const html = render(
      'en',
      <BudgetStep {...props({ ...emptyDraft(), budget: '5' }, ['errors.BUDGET_BELOW_MINIMUM'])} />,
    );
    expect(html).toContain(catalogues.en.errors.BUDGET_BELOW_MINIMUM);
  });

  it.each(locales)('marks windows that have already started as unavailable, in %s', (locale) => {
    const draft: TaskDraft = { ...emptyDraft(), dateMode: 'date', date: '2026-11-19' };
    const html = render(locale, <WhenStep {...props(draft)} now={NOW} locale={locale} />);
    expect(html).toMatch(/disabled=""[^>]*data-testid="post-window-MORNING"/);
    expect(html).not.toMatch(/disabled=""[^>]*data-testid="post-window-AFTERNOON"/);
    expect(html).toContain(message(locale, 'postTask.when.limit'));
  });
});

describe('the review (mockup 3g)', () => {
  const draft: TaskDraft = {
    ...emptyDraft(),
    step: 'review',
    category: 'ERRANDS',
    title: 'Grocery run for my mum',
    description: 'Bread, milk and fruit from Gruž market.',
    address: 'Ulica Ante Starčevića 1, Dubrovnik',
    lat: 42.66,
    lng: 18.08,
    city: 'Dubrovnik',
    dateMode: 'flexible',
    budget: '30,50',
  };

  it.each(locales)('lists every part with the poster’s own amount, in %s', (locale) => {
    const t = (key: string, values?: Record<string, string | number>) => {
      let text = message(locale, key);
      for (const [name, value] of Object.entries(values ?? {}))
        text = text.replace(`{${name}}`, String(value));
      return text;
    };
    const sections = reviewSections(draft, t, locale, 'Mama Kata');
    expect(sections.map((s) => s.step)).toEqual(['what', 'details', 'where', 'when', 'budget']);
    expect(sections[0]!.detail).toBe(message(locale, 'enums.TaskCategory.ERRANDS'));
    expect(sections[2]!.main).toBe(draft.address);
    expect(sections[2]!.detail).toContain('Mama Kata');
    expect(sections[3]!.main).toBe(message(locale, 'taskListing.flexibleDate'));
    expect(sections[4]!.main).toBe(formatMoney(3_050, locale));
  });

  it('shortens a long description', () => {
    expect(excerpt('a'.repeat(200))).toHaveLength(141);
    expect(excerpt('short')).toBe('short');
  });
});
