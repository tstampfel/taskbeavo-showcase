import { TASK_TIME_ZONE, catalogues, locales } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ErrorView } from './error-view';

describe('ErrorView', () => {
  it.each(locales)('shows the error screen and its retry button in %s', (locale) => {
    const text = catalogues[locale].errorBoundary;
    const markup = renderToStaticMarkup(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogues[locale]}
        timeZone={TASK_TIME_ZONE}
      >
        <ErrorView onRetry={() => undefined} />
      </NextIntlClientProvider>,
    );
    expect(markup).toContain('role="alert"');
    expect(markup).toContain(text.title);
    expect(markup).toContain(`>${text.retry}</button>`);
  });
});
