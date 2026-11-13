import type { Locale } from '@taskbeavo/i18n';
import { colors, minTapTarget } from '@taskbeavo/ui-tokens';
import { getTranslations } from 'next-intl/server';

/** Translated 404 body shared by the locale and global not-found pages. */
export async function NotFoundView({ locale }: { locale: Locale }) {
  const t = await getTranslations({ locale, namespace: 'notFound' });
  return (
    <main
      data-testid="not-found"
      style={{ maxWidth: 720, margin: '0 auto', padding: '64px 16px', boxSizing: 'border-box' }}
    >
      <h1 style={{ color: colors.green, fontSize: '2rem', margin: 0 }}>{t('title')}</h1>
      <p>{t('body')}</p>
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
        {t('backHome')}
      </a>
    </main>
  );
}
