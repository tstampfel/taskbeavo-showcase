import { LINK_ID } from '@taskbeavo/app-links';
import { isLocale, type Locale } from '@taskbeavo/i18n';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { use } from 'react';
import styles from './link-target.module.css';
import { AppShell, type AppHref } from './shell/app-shell';
import { Card } from './ui/card';

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
  // The app's centred column and card (BEAV-212); still no data and no action.
  return (
    <AppShell title={t(`linkTarget.${target}`)} back={back}>
      <main className={styles.main} data-testid={`link-target-${target}`}>
        <Card className={styles.card}>
          <p className={styles.body}>{t('linkTarget.notReady')}</p>
          <p className={styles.muted}>{t('linkTarget.safety')}</p>
          <a href={`/${locale}`} className={styles.home}>
            {t('notFound.backHome')}
          </a>
        </Card>
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
