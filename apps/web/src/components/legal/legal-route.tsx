import { LEGAL_PAGE_KINDS, legalPath, type LegalPage } from '@taskbeavo/app-links';
import { LegalPageDocument, LegalVersionPageDocument } from '@taskbeavo/graphql';
import { isLocale, toApiLocale, type Locale } from '@taskbeavo/i18n';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { connection } from 'next/server';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { query } from '../../apollo/rsc';
import { localeAlternates, siteUrl } from '../../i18n/metadata';
import { LegalDocumentView, type LegalPageData } from './legal-document-view';

/** The same rule as the API's version strings (legal/services.py). */
const VERSION = /^[A-Za-z0-9._-]{1,32}$/;

interface LegalParams {
  params: Promise<{ locale: string; version?: string }>;
}

/**
 * Loads one legal page from the API: the version in force, or `version` when given. Rendered
 * per request (never at build time), so a new version shows from its effective date.
 */
export async function loadLegalPage(
  page: LegalPage,
  locale: Locale,
  version?: string,
): Promise<LegalPageData> {
  await connection();
  const kind = LEGAL_PAGE_KINDS[page];
  const language = toApiLocale(locale);
  if (version === undefined) {
    const { data } = await query({ query: LegalPageDocument, variables: { kind, language } });
    if (!data) throw new Error('The legal page query returned no data.');
    return {
      document: data.currentLegalDocument ?? null,
      currentId: data.currentLegalDocument?.id ?? null,
      versions: data.legalDocumentVersions,
      usesGeolite2: data.usesGeolite2,
    };
  }
  const { data } = await query({
    query: LegalVersionPageDocument,
    variables: { kind, language, version },
  });
  if (!data) throw new Error('The legal version query returned no data.');
  return {
    document: data.legalDocument ?? null,
    currentId: data.currentLegalDocument?.id ?? null,
    versions: data.legalDocumentVersions,
    usesGeolite2: data.usesGeolite2,
  };
}

/** The page module (default export and metadata) for one legal page, at its current or one version. */
export function legalRoute(page: LegalPage) {
  function checked(locale: string, version: string | undefined): Locale {
    if (!isLocale(locale) || (version !== undefined && !VERSION.test(version))) notFound();
    return locale;
  }

  async function generateMetadata({ params }: LegalParams): Promise<Metadata> {
    const { locale: raw, version } = await params;
    const locale = checked(raw, version);
    const t = await getTranslations({ locale, namespace: 'legal' });
    const data = await loadLegalPage(page, locale, version);
    const title = t(`documents.${LEGAL_PAGE_KINDS[page]}`);
    const description = t('metaDescription', { document: title });
    const path = `/${page}${version ? `/${encodeURIComponent(version)}` : ''}`;
    // Only the version in force, approved by the lawyer, belongs in search results.
    const indexable = !version && !!data.document && !data.document.isDraft;
    return {
      title,
      description,
      alternates: localeAlternates(locale, path),
      robots: indexable ? undefined : { index: false, follow: true },
      openGraph: {
        type: 'article',
        title,
        description,
        locale,
        url: new URL(`/${locale}${path}`, siteUrl()).toString(),
        siteName: 'TaskBeavo',
      },
      twitter: { card: 'summary', title, description },
    };
  }

  async function Page({ params }: LegalParams) {
    const { locale: raw, version } = await params;
    const locale = checked(raw, version);
    setRequestLocale(locale);
    const data = await loadLegalPage(page, locale, version);
    // An unknown or withdrawn version is a 404; the page itself always renders.
    if (version !== undefined && !data.document) notFound();
    const t = await getTranslations({ locale, namespace: 'legal' });
    const common = await getTranslations({ locale, namespace: 'common' });
    const base = siteUrl();
    const breadcrumbs = {
      '@context': 'https://schema.org',
      '@type': 'BreadcrumbList',
      itemListElement: [
        {
          '@type': 'ListItem',
          position: 1,
          name: common('appName'),
          item: new URL(`/${locale}`, base).toString(),
        },
        {
          '@type': 'ListItem',
          position: 2,
          name: t(`documents.${LEGAL_PAGE_KINDS[page]}`),
          item: new URL(legalPath(page, locale), base).toString(),
        },
      ],
    };
    return (
      <>
        <script
          type="application/ld+json"
          // Built here from catalogue strings and URLs only; `<` escaped so it can't close the tag.
          dangerouslySetInnerHTML={{
            __html: JSON.stringify(breadcrumbs).replace(/</g, '\\u003c'),
          }}
        />
        <LegalDocumentView page={page} locale={locale} data={data} />
      </>
    );
  }

  return { Page, generateMetadata };
}
