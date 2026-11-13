import { TASK_TIME_ZONE, catalogues, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { RankingView } from './ranking-view';

function html(locale: Locale) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      <RankingView locale={locale} />
    </NextIntlClientProvider>,
  );
}

describe('RankingView', () => {
  it('describes the order the offers list uses, as a draft', () => {
    const markup = html('en');

    expect(markup).toContain('data-testid="ranking-draft"');
    expect(markup).toContain('>How offers are ordered</h1>');
    expect(markup).toContain('listed newest first');
    expect(markup).toContain('Lowest price');
    expect(markup).toContain('nobody can pay for a better position');
    expect(markup).toContain('href="/en/fees"');
  });

  it.each(locales)('renders in %s', (locale) => {
    expect(html(locale)).toContain(`href="/${locale}/fees"`);
  });

  it.each(locales)('shows the %s page in its own language, with no English left', (locale) => {
    const markup = html(locale);
    expect(markup).not.toContain('lang="en"');
    if (locale !== 'en') expect(markup).not.toContain('How offers are ordered');
  });
});
