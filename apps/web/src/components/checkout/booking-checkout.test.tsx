import { TASK_TIME_ZONE, catalogues, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { DemoPayForm, PAYMENT_ELEMENT_OPTIONS } from './booking-checkout';

describe('PAYMENT_ELEMENT_OPTIONS', () => {
  it('keeps Stripe Link off, so no Stripe login is stored on the device', () => {
    expect(PAYMENT_ELEMENT_OPTIONS.wallets.link).toBe('never');
  });
});

function demoForm(locale: Locale) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      <DemoPayForm
        secret="pi_demo1_secret_x"
        locale={locale}
        totalCents={3_360}
        retry={false}
        onSubmitted={() => undefined}
      />
    </NextIntlClientProvider>,
  );
}

describe('DemoPayForm', () => {
  it.each(locales)('says plainly that it is a demo payment with no real money (%s)', (locale) => {
    const markup = demoForm(locale);

    expect(markup).toContain(catalogues[locale].checkout.demo.label);
    expect(markup).toContain('4242 4242 4242 4242');
    expect(markup).toContain('4000 0000 0000 0002');
    expect(markup).toContain('data-testid="checkout-pay"');
  });

  it('frames the demo card in its own card and pins the pay button (mockup 6g)', () => {
    const markup = demoForm('en');
    const demo = markup.split('data-testid="checkout-demo-card"')[1]!;
    const foot = markup.split('data-testid="checkout-foot"')[1]!;

    expect(demo).toContain(catalogues.en.checkout.demo.label);
    expect(demo).toContain('4242 4242 4242 4242');
    expect(foot).toContain('data-testid="checkout-pay"');
    expect(foot).not.toContain('4242');
  });
});
