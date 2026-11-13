'use client';

import { useMutation } from '@apollo/client/react';
import { SetLocaleDocument } from '@taskbeavo/graphql';
import {
  createAccountLocaleSaver,
  languageNames,
  locales,
  toApiLocale,
  type Locale,
} from '@taskbeavo/i18n';
import { useSearchParams } from 'next/navigation';
import { useLocale, useTranslations } from 'next-intl';
import { Suspense } from 'react';
import { Link, usePathname } from '../i18n/navigation';
import { savePickedLocale } from '../i18n/picked-locale';
import styles from './language-switcher.module.css';

type Query = Record<string, string | string[]>;

// One per tab, so quick successive picks leave the account on the last one.
const saveLatest = createAccountLocaleSaver();

/**
 * HR / EN / DE / FR, as in the mockups' language control. Each option links to the same page,
 * query string included, in that language; opening it updates the locale cookie. A signed-in
 * user's choice is also saved to their account; signed out, the API answers UNAUTHENTICATED
 * and the cookie alone remembers it.
 */
export function LanguageSwitcher() {
  // Statically rendered pages get the options without the query; the browser adds it.
  return (
    <Suspense fallback={<LanguageOptions query={{}} />}>
      <LanguageOptionsWithQuery />
    </Suspense>
  );
}

function LanguageOptionsWithQuery() {
  const searchParams = useSearchParams();
  const query: Query = {};
  for (const key of new Set(searchParams.keys())) {
    const values = searchParams.getAll(key);
    query[key] = values.length === 1 ? (values[0] ?? '') : values;
  }
  return <LanguageOptions query={query} />;
}

function LanguageOptions({ query }: { query: Query }) {
  const t = useTranslations('languageSwitcher');
  const current = useLocale();
  const pathname = usePathname();
  // keepalive lets the request finish while the page navigates to the other language.
  const [setLocale] = useMutation(SetLocaleDocument, {
    context: { fetchOptions: { keepalive: true } },
  });

  function choose(locale: Locale) {
    if (locale === current) return;
    // An explicit pick: sign-in asks before replacing it with the account's language.
    savePickedLocale(locale);
    void saveLatest(toApiLocale(locale), (apiLocale) =>
      setLocale({ variables: { locale: apiLocale } }),
    );
  }

  return (
    <nav aria-label={t('label')} data-testid="language-switcher">
      <ul className={styles.list}>
        {locales.map((locale) => (
          <li key={locale}>
            <Link
              href={{ pathname, query }}
              locale={locale}
              lang={locale}
              hrefLang={locale}
              aria-label={languageNames[locale]}
              aria-current={locale === current ? 'true' : undefined}
              className={styles.option}
              onClick={() => choose(locale)}
            >
              {locale.toUpperCase()}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
