'use client';

import { useQuery } from '@apollo/client/react';
import { TermsStatusDocument, ViewerDocument } from '@taskbeavo/graphql';
import { toApiLocale, type Locale } from '@taskbeavo/i18n';
import { useLocale, useTranslations } from 'next-intl';
import { useEffect } from 'react';
import { Link, usePathname, useRouter } from '../../i18n/navigation';
import { markTermsUpdateShown, termsUpdateShown } from './shown';
import own from './terms-update.module.css';

const PAGE = '/terms-update';

/**
 * Limited mode after a terms change (mockup 21c). While `viewer.mustAcceptTerms`, the first
 * page this tab shows the account (a new tab, or another account signing in) goes to "We've
 * updated our terms", unless the visit started there; after "Not now" every page says why
 * posting and booking are refused, and links back to it. The API enforces the limit
 * (TERMS_UPDATE_REQUIRED); this only explains it.
 */
export function TermsBanner() {
  const t = useTranslations('termsUpdate');
  const pathname = usePathname();
  const router = useRouter();
  const locale = useLocale() as Locale;
  // The account menu's viewer, from the cache: signed out, nothing more is asked.
  const viewer = useQuery(ViewerDocument).data?.viewer;
  const { data } = useQuery(TermsStatusDocument, {
    variables: { language: toApiLocale(locale) },
    fetchPolicy: 'cache-and-network',
    skip: !viewer,
  });
  const mustAccept = Boolean(viewer) && data?.viewer?.mustAcceptTerms === true;
  const onPage = pathname === PAGE;
  const viewerId = viewer?.id;
  const version = data?.viewer?.termsToAccept?.version;

  useEffect(() => {
    if (!mustAccept || onPage || !viewerId || termsUpdateShown(viewerId, version)) return;
    markTermsUpdateShown(viewerId, version);
    router.push({ pathname: PAGE, query: { next: pathname } });
  }, [mustAccept, onPage, viewerId, version, pathname, router]);

  if (!mustAccept || onPage) return null;
  return (
    <aside className={own.banner} data-testid="terms-banner">
      <strong>{t('bannerTitle')}</strong>
      <span>{t('bannerBody')}</span>
      <Link className={own.link} href={{ pathname: PAGE, query: { next: pathname } }}>
        {t('bannerAction')}
      </Link>
    </aside>
  );
}
