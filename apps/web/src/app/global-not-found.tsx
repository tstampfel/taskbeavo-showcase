import { defaultLocale } from '@taskbeavo/i18n';
import { colors } from '@taskbeavo/ui-tokens';
import type { Metadata } from 'next';
import { Manrope } from 'next/font/google';
import { getTranslations } from 'next-intl/server';
import { NotFoundView } from '../components/not-found-view';

const manrope = Manrope({ subsets: ['latin', 'latin-ext'], display: 'swap' });

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations({ locale: defaultLocale, namespace: 'notFound' });
  return { title: t('title') };
}

/** 404 for paths with no locale (the proxy skips files and Next internals), shown in Croatian. */
export default function GlobalNotFound() {
  return (
    <html lang={defaultLocale}>
      <body
        className={manrope.className}
        style={{ margin: 0, background: colors.offWhite, color: colors.ink }}
      >
        <NotFoundView locale={defaultLocale} />
      </body>
    </html>
  );
}
