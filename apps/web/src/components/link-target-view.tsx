import { LINK_ID } from '@taskbeavo/app-links';
import { isLocale, type Locale } from '@taskbeavo/i18n';
import { colors, minTapTarget } from '@taskbeavo/ui-tokens';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { use } from 'react';

/** The pages that mirror app screens; each has its title under `linkTarget.<target>`. */
export type LinkTarget = 'task' | 'release' | 'noAccess' | 'conversation' | 'payouts';

interface LinkTargetParams {
  /** `id` is absent on pages without one (`/de/helper/payouts`). */
  params: Promise<{ locale: string; id?: string }>;
}

/**
 * Placeholder body for a page an email or push links to (`/de/bookings/7/release`), until the
 * real page is built. It renders no data and no action, so opening a link changes nothing.
 */
export async function LinkTargetView({ locale, target }: { locale: Locale; target: LinkTarget }) {
  const t = await getTranslations({ locale });
  return (
    <main
      data-testid={`link-target-${target}`}
      style={{ maxWidth: 720, margin: '0 auto', padding: '64px 16px', boxSizing: 'border-box' }}
    >
      <h1 style={{ color: colors.green, fontSize: '2rem', margin: 0 }}>
        {t(`linkTarget.${target}`)}
      </h1>
      <p>{t('linkTarget.notReady')}</p>
      <p>{t('linkTarget.safety')}</p>
      <a
        href={`/${locale}`}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          minHeight: minTapTarget,
          padding: '0 16px',
          borderRadius: 8,
          background: colors.green,
          color: colors.offWhite,
          fontWeight: 700,
          textDecoration: 'none',
        }}
      >
        {t('notFound.backHome')}
      </a>
    </main>
  );
}

/** The page module (default export and metadata) for one link target. */
export function linkTargetPage(target: LinkTarget) {
  async function generateMetadata({ params }: LinkTargetParams): Promise<Metadata> {
    const { locale } = await params;
    if (!isLocale(locale)) return {};
    const t = await getTranslations({ locale });
    // Private pages: never in search results.
    return { title: t(`linkTarget.${target}`), robots: { index: false, follow: false } };
  }

  function Page({ params }: LinkTargetParams) {
    const { locale, id } = use(params);
    if (!isLocale(locale) || (id !== undefined && !LINK_ID.test(id))) notFound();
    setRequestLocale(locale);
    return <LinkTargetView locale={locale} target={target} />;
  }

  return { Page, generateMetadata };
}
