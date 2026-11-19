import { TASK_TIME_ZONE, catalogues, formatMoney, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { Listing } from './listing-text';
import { BrowseView, TaskPageView, browseHref } from './task-views';

vi.mock('../../i18n/navigation', () => ({
  Link: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

const plain = (text: string) => text.replace(/[\u00a0\u202f]/g, ' ');

function html(locale: Locale, node: ReactNode) {
  return plain(
    renderToStaticMarkup(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogues[locale]}
        timeZone={TASK_TIME_ZONE}
      >
        {node}
      </NextIntlClientProvider>,
    ),
  );
}

function listing(id: string, extra: Partial<Listing> = {}): Listing {
  return {
    id,
    category: 'GARDEN',
    title: `Mow the lawn ${id}`,
    description: 'Front lawn and the hedge.',
    approximateLat: 42.66,
    approximateLng: 18.09,
    serviceAreaName: 'Dubrovnik',
    windowStart: '2026-11-22T08:00:00Z',
    windowEnd: '2026-11-22T10:00:00Z',
    flexibleDate: false,
    budgetCents: 3_500,
    photos: [],
    isMine: false,
    publishedAt: '2026-11-19T10:00:00Z',
    ...extra,
  };
}

describe('browseHref', () => {
  it('keeps the filter in the query', () => {
    expect(browseHref({})).toBe('/tasks');
    expect(browseHref({ areaSlug: 'dubrovnik', category: 'GARDEN', after: 'c1' })).toBe(
      '/tasks?area=dubrovnik&category=GARDEN&after=c1',
    );
  });
});

describe('BrowseView', () => {
  it.each(locales)('lists open tasks newest first in %s', (locale) => {
    const messages = catalogues[locale];
    const out = html(
      locale,
      <BrowseView
        locale={locale}
        filter={{}}
        page={{ items: [listing('a'), listing('b')], endCursor: 'c1', hasMore: true }}
      />,
    );
    expect(out).toContain(messages.browse.title);
    expect(out).toContain(messages.browse.order);
    expect(out.indexOf('Mow the lawn a')).toBeLessThan(out.indexOf('Mow the lawn b'));
    expect(out).toContain(messages.taskListing.area.replace('{area}', 'Dubrovnik'));
    expect(out).toContain(
      messages.taskListing.budget.replace('{amount}', plain(formatMoney(3_500, locale))),
    );
    expect(out).toContain('href="/tasks/a"');
    expect(out).toContain('href="/tasks?after=c1"');
    for (const category of Object.values(messages.enums.TaskCategory)) {
      expect(out).toContain(category);
    }
  });

  it('marks the chosen category and says when nothing is open', () => {
    const out = html(
      'en',
      <BrowseView
        locale="en"
        filter={{ category: 'ERRANDS', after: 'c1' }}
        page={{ items: [], endCursor: null, hasMore: false }}
      />,
    );
    expect(out).toContain(catalogues.en.browse.empty);
    expect(out).toMatch(/aria-current="true"[^>]*data-testid="browse-category-ERRANDS"/);
    expect(out).toContain(catalogues.en.browse.firstPage);
    expect(out).not.toContain('data-testid="browse-more"');
  });
});

describe('TaskPageView', () => {
  it.each(locales)('shows the task with the approximate area only in %s', (locale) => {
    const messages = catalogues[locale];
    const out = html(
      locale,
      <TaskPageView
        locale={locale}
        task={listing('a', {
          photos: [{ id: 'p1', url: 'https://photos.example.com/p1.jpg' }],
        })}
      />,
    );
    expect(out).toContain('<h1');
    expect(out).toContain('Mow the lawn a');
    expect(out).toContain(messages.enums.TaskCategory.GARDEN);
    expect(out).toContain(messages.taskListing.area.replace('{area}', 'Dubrovnik'));
    expect(out).toContain(messages.taskListing.approximateNote);
    expect(out).toContain(`alt="${messages.taskListing.photoAlt.replace('{number}', '1')}"`);
  });

  it('says a flexible task suits any day', () => {
    const out = html(
      'en',
      <TaskPageView
        locale="en"
        task={listing('a', { flexibleDate: true, windowStart: null, windowEnd: null })}
      />,
    );
    expect(out).toContain(catalogues.en.taskListing.flexibleDate);
  });
});
