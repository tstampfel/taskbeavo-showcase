import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { LEGAL_PAGES } from '@taskbeavo/app-links';
import { TASK_TIME_ZONE, catalogues, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { LegalDocumentView, LegalFooter, type LegalPageData } from './legal-document-view';

const NOW = new Date('2026-12-15T12:00:00Z');

function html(locale: Locale, node: ReactNode) {
  return renderToStaticMarkup(
    <NextIntlClientProvider locale={locale} messages={catalogues[locale]} timeZone={TASK_TIME_ZONE}>
      {node}
    </NextIntlClientProvider>,
  );
}

const v1 = { id: '1', version: '1.1', effectiveAt: '2026-11-01T00:00:00Z', isDraft: false };
const v2 = { id: '2', version: '1.2', effectiveAt: '2026-12-01T00:00:00Z', isDraft: false };
const v3 = { id: '3', version: '1.3', effectiveAt: '2027-01-15T00:00:00Z', isDraft: false };

function data(overrides: Partial<LegalPageData> = {}): LegalPageData {
  return {
    document: {
      ...v2,
      kind: 'TERMS',
      language: 'DE',
      text: '# 1. Was TaskBeavo macht\n\nTaskBeavo vermittelt Aufgaben.',
    },
    currentId: '2',
    versions: [v3, v2, v1],
    usesGeolite2: false,
    ...overrides,
  };
}

describe('LegalDocumentView', () => {
  it('shows the document title, version, effective date and the text in its language', () => {
    const markup = html(
      'de',
      <LegalDocumentView page="terms" locale="de" data={data()} now={NOW} />,
    );

    expect(markup).toContain('<h1');
    expect(markup).toContain('>AGB</h1>');
    expect(markup).toContain('Version 1.2');
    expect(markup).toContain('Gültig ab 1. Dezember 2026');
    expect(markup).toContain('lang="de"');
    expect(markup).toContain('<h2>1. Was TaskBeavo macht</h2>');
    expect(markup).not.toContain('data-testid="legal-draft"');
    expect(markup).not.toContain('data-testid="legal-not-current"');
  });

  it('links every other version, and the version in force without a version in its path', () => {
    const markup = html(
      'en',
      <LegalDocumentView
        page="terms"
        locale="en"
        data={data({ document: { ...data().document!, ...v1 } })}
        now={NOW}
      />,
    );

    expect(markup).toContain('href="#versions"');
    expect(markup).toContain('href="/en/terms"');
    expect(markup).toContain('href="/en/terms/1.3"');
    expect(markup).not.toContain('href="/en/terms/1.1"');
    expect(markup).toContain('This is an earlier version');
    expect(markup).toContain('Read the version in force now');
    expect(markup).toContain('upcoming');
  });

  it('says a scheduled version is not yet in force and links the version in force', () => {
    const scheduled = (locale: Locale, overrides: Partial<LegalPageData> = {}) =>
      html(
        locale,
        <LegalDocumentView
          page="terms"
          locale={locale}
          data={data({ document: { ...data().document!, ...v3 }, ...overrides })}
          now={NOW}
        />,
      );

    expect(scheduled('en')).toContain(
      'Not yet in force – applies from January 15, 2027; the version in force is <a',
    );
    expect(scheduled('en')).toContain('href="/en/terms">version 1.2</a>.</span>');
    expect(scheduled('fr')).toContain('Entre en vigueur le 15 janvier 2027');
    expect(scheduled('fr')).toContain('href="/fr/terms">version 1.2</a>');
    // Nothing in force yet: the date alone, no link.
    const first = scheduled('en', { currentId: null, versions: [v3] });
    expect(first).toContain('Not yet in force – applies from January 15, 2027.');
    expect(first).not.toContain('version 1.2</a>');
  });

  it.each(locales)('never implies acceptance by continued use in %s', (locale) => {
    const { scheduledNotice, scheduledNoticeNoCurrent } = catalogues[locale].legal;
    for (const message of [scheduledNotice, scheduledNoticeNoCurrent]) {
      expect(message).not.toMatch(/continu|weiter|nastav|accept|prihva|akzept/i);
    }
  });

  it.each(locales)('marks a draft clearly in %s', (locale) => {
    const draft = { ...data().document!, isDraft: true };
    const markup = html(
      locale,
      <LegalDocumentView
        page="privacy"
        locale={locale}
        data={data({ document: draft })}
        now={NOW}
      />,
    );

    expect(markup).toContain('data-testid="legal-draft"');
    expect(markup).toContain(catalogues[locale].legal.draftTitle);
  });

  it('says the document is not published yet when there is none', () => {
    const markup = html(
      'hr',
      <LegalDocumentView
        page="cookies"
        locale="hr"
        data={data({ document: null, currentId: null, versions: [] })}
        now={NOW}
      />,
    );

    expect(markup).toContain('>Pravila o kolačićima</h1>');
    expect(markup).toContain('Još nije objavljeno');
    expect(markup).not.toContain('id="versions"');
  });

  it("shows MaxMind's attribution on the imprint only while GeoLite2 is in use", () => {
    const imprint = (usesGeolite2: boolean, page: 'imprint' | 'terms' = 'imprint') =>
      html(
        'de',
        <LegalDocumentView page={page} locale="de" data={data({ usesGeolite2 })} now={NOW} />,
      );

    expect(imprint(true)).toContain(
      'This product includes GeoLite Data created by MaxMind, available from <a href="https://www.maxmind.com">https://www.maxmind.com</a>.',
    );
    expect(imprint(false)).not.toContain('GeoLite');
    expect(imprint(true, 'terms')).not.toContain('GeoLite');
  });

  it('puts the Croatian sentence first in hr and keeps the English original in every language', () => {
    const attribution = (locale: Locale) => {
      const markup = html(
        locale,
        <LegalDocumentView page="imprint" locale={locale} data={data({ usesGeolite2: true })} />,
      );
      return markup.slice(markup.indexOf('data-testid="legal-geolite"'));
    };
    const english = 'This product includes GeoLite Data created by MaxMind';

    expect(attribution('en').match(/This product includes/g)).toHaveLength(1);
    const hr = attribution('hr');
    expect(hr.indexOf('Ovaj proizvod uključuje GeoLite podatke')).toBeGreaterThan(-1);
    expect(hr.indexOf('Ovaj proizvod')).toBeLessThan(hr.indexOf(english));
    for (const [locale, translated] of [
      ['de', 'Dieses Produkt enthält GeoLite-Daten'],
      ['fr', 'Ce produit comprend des données GeoLite'],
    ] as const) {
      const markup = attribution(locale);
      expect(markup.indexOf(english)).toBeGreaterThan(-1);
      expect(markup.indexOf(english)).toBeLessThan(markup.indexOf(translated));
    }
    expect(hr).toContain(`<span lang="en">${english}`);
    // The translation names its own language: in the privacy text the surrounding lang is the
    // document's (review R1-8).
    expect(hr).toContain('<span lang="hr">Ovaj proizvod');
    expect(attribution('de')).toContain('<span lang="de">Dieses Produkt');
  });

  it("shows MaxMind's attribution in the privacy notice where the lawyer marked it", () => {
    const privacy = (text: string, usesGeolite2 = true) =>
      html(
        'en',
        <LegalDocumentView
          page="privacy"
          locale="en"
          data={data({
            document: { ...data().document!, kind: 'PRIVACY', language: 'EN', text },
            usesGeolite2,
          })}
          now={NOW}
        />,
      );

    const marked = privacy(
      '## New sign-in email\n\nWe name the city.\n\n<!-- geolite-attribution -->\n\n## Next',
    );
    const at = marked.indexOf('data-testid="legal-geolite"');
    expect(marked.indexOf('We name the city.')).toBeLessThan(at);
    expect(marked.indexOf('>Next</h')).toBeGreaterThan(at);
    // Unmarked text: after the text. Not in use: nowhere.
    const unmarked = privacy('## New sign-in email\n\nWe name the city.');
    expect(unmarked.indexOf('data-testid="legal-geolite"')).toBeGreaterThan(
      unmarked.indexOf('We name the city.'),
    );
    expect(privacy('Text', false)).not.toContain('GeoLite');
  });

  it('links the price list from the imprint (BEAV-191)', () => {
    const page = (name: 'imprint' | 'terms') =>
      html('de', <LegalDocumentView page={name} locale="de" data={data()} now={NOW} />);

    expect(page('imprint')).toContain(
      'Unsere Preise und die Servicegebühr stehen in der <a href="/de/fees">Preisliste</a>.',
    );
    expect(page('terms')).not.toContain('data-testid="imprint-fees"');
    // The legal nav on every legal page lists it too.
    expect(page('terms')).toContain('href="/de/fees"');
  });

  it('links the six legal pages, marking the current one', () => {
    const markup = html(
      'en',
      <LegalDocumentView page="cancellation" locale="en" data={data()} now={NOW} />,
    );

    for (const page of LEGAL_PAGES) expect(markup).toContain(`href="/en/${page}"`);
    expect(markup).toContain('href="/en/cancellation" aria-current="page"');
  });
});

describe('LegalFooter', () => {
  it.each(locales)('links the legal pages, the price list and the ranking in %s', (locale) => {
    const markup = html(locale, <LegalFooter locale={locale} />);

    for (const page of LEGAL_PAGES) expect(markup).toContain(`href="/${locale}/${page}"`);
    expect(markup).toContain(`href="/${locale}/fees"`);
    expect(markup).toContain(`href="/${locale}/ranking"`);
  });
});

describe('legal routes', () => {
  it.each(LEGAL_PAGES)('/%s has a page and a version page', (page) => {
    const dir = join(__dirname, '../../app/[locale]', page);
    expect(existsSync(join(dir, 'page.tsx'))).toBe(true);
    expect(existsSync(join(dir, '[version]', 'page.tsx'))).toBe(true);
  });
});
