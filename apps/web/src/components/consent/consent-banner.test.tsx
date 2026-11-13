import { TASK_TIME_ZONE, catalogues, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ConsentPanel, type ConsentPanelProps } from './consent-banner';

function html(locale: Locale, node: ReactNode) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      {node}
    </NextIntlClientProvider>,
  );
}

const props: ConsentPanelProps = {
  initialDetails: false,
  initialAnalytics: false,
  saving: false,
  failed: false,
  onChoose: () => undefined,
};

/** The class list of the button with `testId`. */
function buttonClass(markup: string, testId: string): string | undefined {
  return new RegExp(`<button[^>]*class="([^"]*)"[^>]*data-testid="${testId}"`).exec(markup)?.[1];
}

describe('ConsentPanel', () => {
  for (const locale of locales) {
    it(`offers Reject as plainly as Accept, and links the cookie policy, in ${locale}`, () => {
      const markup = html(locale, <ConsentPanel {...props} />);
      const t = catalogues[locale].consent;

      expect(markup).toContain(t.title);
      expect(markup).toContain(t.reject);
      expect(markup).toContain(t.accept);
      expect(buttonClass(markup, 'consent-reject')).toBeDefined();
      expect(buttonClass(markup, 'consent-reject')).toBe(buttonClass(markup, 'consent-accept'));
      expect(markup).toContain(`href="/${locale}/cookies"`);
    });
  }

  it('shows analytics switched off in Settings until the visitor turns it on', () => {
    const markup = html('en', <ConsentPanel {...props} initialDetails />);

    expect(markup).toContain('Cookie settings');
    const analyticsSwitch = /<input[^>]*data-testid="consent-analytics"[^>]*>/.exec(markup)?.[0];
    expect(analyticsSwitch).toBeDefined();
    expect(analyticsSwitch).not.toContain('checked');
    expect(markup).toContain('data-testid="consent-save"');
  });

  it('says when the choice could not be saved', () => {
    expect(html('de', <ConsentPanel {...props} failed />)).toContain(
      catalogues.de.consent.saveFailed,
    );
  });
});
