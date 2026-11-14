import type { CancelView } from '@taskbeavo/graphql';
import { TASK_TIME_ZONE, catalogues, formatMoney, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { PosterCancel } from './booking-cancel';

vi.mock('../../i18n/navigation', () => ({
  Link: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const view: CancelView & { kind: 'cancel' } = {
  kind: 'cancel',
  preview: {
    case: 'LATE',
    chargeCents: 3_360,
    refundCents: 2_521,
    helperCompensationCents: 750,
    keptFeeCents: 89,
    creditBackCents: 0,
    full: false,
  },
  withdrawal: null,
  noFault: true,
};

function render(locale: Locale, withdrawal: ReactNode = null) {
  const html = renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      <PosterCancel
        view={view}
        reason={{ kind: 'ordinary', key: 'OTHER' }}
        onPick={() => undefined}
        money={(cents) => formatMoney(cents, locale)}
        busy={false}
        cancelling={false}
        withdrawal={withdrawal}
        onNoFault={() => undefined}
        onCancel={() => undefined}
      />
    </NextIntlClientProvider>,
  );
  return html.replaceAll('&#x27;', "'").replaceAll('&quot;', '"');
}

describe('PosterCancel (mockup 13b, BEAV-211)', () => {
  it.each(locales)('opens on the reason step in %s, with no amounts yet', (locale) => {
    const html = render(locale);
    const t = catalogues[locale].cancel;
    expect(html).toContain('data-testid="cancel-step-reason"');
    expect(html).toContain(t.seeRefund);
    expect(html).toContain(t.reasonsLegend);
    expect(html).toContain(t.noFaultLegend);
    expect(html).not.toContain('cancel-refund-rows');
    expect(html).not.toContain(formatMoney(2_521, locale));
  });

  it('keeps the trader withdrawal on the reason step', () => {
    const html = render('en', <section data-testid="cancel-withdrawal" />);
    expect(html).toContain('data-testid="cancel-withdrawal"');
  });
});
