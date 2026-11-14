import { LINK_ID } from '@taskbeavo/app-links';
import { isLocale, type Locale } from '@taskbeavo/i18n';
import { colors, minTapTarget } from '@taskbeavo/ui-tokens';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { use } from 'react';
import { AppShell, type AppHref } from './shell/app-shell';

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
export async function LinkTargetView({
  locale,
  target,
  back,
}: {
  locale: Locale;
  target: LinkTarget;
  back?: AppHref;
}) {
  const t = await getTranslations({ locale });
  return (
    <AppShell title={t(`linkTarget.${target}`)} back={back}>
      <main
        data-testid={`link-target-${target}`}
        style={{ maxWidth: 720, margin: '0 auto', padding: '16px', boxSizing: 'border-box' }}
      >
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
    </AppShell>
  );
}

/**
 * The page module (default export and metadata) for one link target. `parent` gives the page
 * the top bar's back button opens, from the route's `id` when it has one.
 */
export function linkTargetPage(target: LinkTarget, parent?: (id: string | undefined) => AppHref) {
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
    return <LinkTargetView locale={locale} target={target} back={parent?.(id)} />;
  }

  return { Page, generateMetadata };
}
