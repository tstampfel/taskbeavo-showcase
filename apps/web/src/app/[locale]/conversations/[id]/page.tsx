import { LINK_ID } from '@taskbeavo/app-links';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations, setRequestLocale } from 'next-intl/server';
import { ConversationView } from '../../../../components/chat/conversation-view';
import styles from '../../../../components/chat/chat.module.css';
import { routing } from '../../../../i18n/routing';
import { AppShell } from '../../../../components/shell/app-shell';

type Props = PageProps<'/[locale]/conversations/[id]'>;

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'chat' });
  return { title: t('metaTitle'), robots: { index: false, follow: false } };
}

/** The private chat of a poster and a helper (BEAV-104, mockup 5a); message pushes open it. */
export default async function ConversationPage({ params }: Props) {
  const { locale, id } = await params;
  if (!hasLocale(routing.locales, locale) || !LINK_ID.test(id)) notFound();
  setRequestLocale(locale);
  const { chat, contactFilter, errors } = await getMessages();
  const t = await getTranslations({ locale, namespace: 'chat' });
  return (
    <AppShell title={t('metaTitle')}>
      <main className={styles.main}>
        <NextIntlClientProvider messages={{ chat, contactFilter, errors }}>
          <ConversationView conversationId={id} locale={locale} />
        </NextIntlClientProvider>
      </main>
    </AppShell>
  );
}
