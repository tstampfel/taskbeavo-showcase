import {
  feesPath,
  LEGAL_PAGE_KINDS,
  LEGAL_PAGES,
  legalPath,
  rankingPath,
  type LegalPage,
} from '@taskbeavo/app-links';
import type {
  LegalDocumentPageFieldsFragment,
  LegalDocumentVersionFieldsFragment,
} from '@taskbeavo/graphql';
import { formatDate, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { CookieSettingsButton } from '../consent/cookie-settings-button';
import { Badge } from '../ui';
import { GeoliteAttribution, splitAtGeoliteMarker } from './geolite-attribution';
import styles from './legal.module.css';
import { Markdown } from './markdown';

export interface LegalPageData {
  /** The version shown; null while none is published (or, in production, only a draft is). */
  document: LegalDocumentPageFieldsFragment | null;
  /** The id of the version in force now, if any. */
  currentId: string | null;
  /** Every readable version, newest effective date first, scheduled ones included. */
  versions: LegalDocumentVersionFieldsFragment[];
  usesGeolite2: boolean;
}

type Status = 'current' | 'earlier' | 'upcoming';

function statusOf(
  version: Pick<LegalDocumentVersionFieldsFragment, 'id' | 'effectiveAt'>,
  currentId: string | null,
  now: Date,
): Status {
  if (version.id === currentId) return 'current';
  return new Date(String(version.effectiveAt)) > now ? 'upcoming' : 'earlier';
}

export interface LegalDocumentViewProps {
  page: LegalPage;
  locale: Locale;
  data: LegalPageData;
  now?: Date;
}

/** Mockup 21e: one legal document with its version, effective date and every other version. */
export function LegalDocumentView({
  page,
  locale,
  data,
  now = new Date(),
}: LegalDocumentViewProps) {
  const t = useTranslations('legal');
  const tFees = useTranslations('fees');
  const { document, currentId, versions } = data;
  const status = document ? statusOf(document, currentId, now) : null;
  const date = (value: unknown) => formatDate(String(value), locale, 'long');

  return (
    <main className={styles.main} data-testid={`legal-${page}`}>
      <article className={styles.article}>
        {document?.isDraft ? (
          <p className={styles.note} role="note" data-testid="legal-draft">
            <strong>{t('draftTitle')}</strong> <span>{t('draftBody')}</span>
          </p>
        ) : null}
        <h1 className={styles.title}>{t(`documents.${LEGAL_PAGE_KINDS[page]}`)}</h1>
        {document ? (
          <>
            <div className={styles.meta} data-testid="legal-meta">
              <Badge>{t('version', { version: document.version })}</Badge>
              <Badge>
                {t(status === 'upcoming' ? 'takesEffect' : 'effectiveFrom', {
                  date: date(document.effectiveAt),
                })}
              </Badge>
              {versions.length > 1 ? (
                <a className={styles.link} href="#versions">
                  {t('versionsLink')}
                </a>
              ) : null}
            </div>
            {status === 'upcoming' ? (
              <ScheduledNotice page={page} locale={locale} data={data} date={date} />
            ) : null}
            {status === 'earlier' ? (
              <p className={`${styles.note} ${styles.info}`} data-testid="legal-not-current">
                <span>{t('earlierNotice')}</span>
                {currentId ? (
                  <a className={styles.link} href={legalPath(page, locale)}>
                    {t('readCurrent')}
                  </a>
                ) : null}
              </p>
            ) : null}
            <LegalBody page={page} locale={locale} data={data} />
          </>
        ) : (
          <div className={`${styles.note} ${styles.info}`} data-testid="legal-unavailable">
            <strong>{t('unavailableTitle')}</strong>
            <span>{t('unavailableBody')}</span>
          </div>
        )}
        {page === 'imprint' ? (
          <p className={styles.attribution} data-testid="imprint-fees">
            {tFees.rich('imprint', {
              link: (chunks) => <a href={feesPath(locale)}>{chunks}</a>,
            })}
          </p>
        ) : null}
        {page === 'imprint' && data.usesGeolite2 ? <GeoliteAttribution locale={locale} /> : null}
        {versions.length > 1 ? (
          <section id="versions" className={styles.versions} aria-labelledby="versions-title">
            <h2 id="versions-title">{t('versionsTitle')}</h2>
            <ul className={styles.versionList} data-testid="legal-versions">
              {versions.map((version) => {
                const versionStatus = statusOf(version, currentId, now);
                const showing = version.id === document?.id;
                const label = `${t('version', { version: version.version })} · ${t(
                  versionStatus === 'upcoming' ? 'takesEffect' : 'effectiveFrom',
                  { date: date(version.effectiveAt) },
                )}`;
                return (
                  <li key={version.id}>
                    {showing ? (
                      <span aria-current="page">{label}</span>
                    ) : (
                      <a
                        className={styles.link}
                        href={legalPath(
                          page,
                          locale,
                          versionStatus === 'current' ? undefined : version.version,
                        )}
                      >
                        {label}
                      </a>
                    )}
                    {versionStatus === 'current' ? (
                      <span className={styles.muted}>{t('versionsCurrent')}</span>
                    ) : null}
                    {versionStatus === 'upcoming' ? (
                      <span className={styles.muted}>{t('versionsUpcoming')}</span>
                    ) : null}
                    {showing ? <span className={styles.muted}>{t('versionsShowing')}</span> : null}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}
      </article>
      <LegalNav page={page} locale={locale} />
    </main>
  );
}

/**
 * A scheduled version's banner: when it applies and a link to the version in force (BEAV-190).
 * It never suggests that continuing to use TaskBeavo accepts it; a change needs re-acceptance.
 */
function ScheduledNotice({
  page,
  locale,
  data,
  date,
}: {
  page: LegalPage;
  locale: Locale;
  data: LegalPageData;
  date: (value: unknown) => string;
}) {
  const t = useTranslations('legal');
  const current = data.versions.find((version) => version.id === data.currentId);
  const applies = date(data.document?.effectiveAt);
  return (
    <p className={`${styles.note} ${styles.info}`} data-testid="legal-not-current">
      <span>
        {current
          ? t.rich('scheduledNotice', {
              date: applies,
              version: current.version,
              link: (chunks) => (
                <a className={styles.link} href={legalPath(page, locale)}>
                  {chunks}
                </a>
              ),
            })
          : t('scheduledNoticeNoCurrent', { date: applies })}
      </span>
    </p>
  );
}

/**
 * The document text. On the privacy notice MaxMind's attribution goes where the lawyer marked the
 * new-sign-in email section (GEOLITE_MARKER), else after the text, while GeoLite2 is in use.
 */
function LegalBody({
  page,
  locale,
  data,
}: {
  page: LegalPage;
  locale: Locale;
  data: LegalPageData;
}) {
  const document = data.document!;
  const attribution = page === 'privacy' && data.usesGeolite2;
  const { before, after } = attribution
    ? splitAtGeoliteMarker(document.text)
    : { before: document.text, after: null };
  return (
    <div className={styles.body} lang={document.language.toLowerCase()} data-testid="legal-body">
      <Markdown source={before} />
      {attribution ? <GeoliteAttribution locale={locale} /> : null}
      {after !== null ? <Markdown source={after} /> : null}
    </div>
  );
}

/** The six legal pages, in the mockup's order, then the price list and the offer ranking
 * (BEAV-191). */
function LegalNav({ page, locale }: { page: LegalPage; locale: Locale }) {
  const t = useTranslations('legal');
  const tFees = useTranslations('fees');
  const tRanking = useTranslations('ranking');
  return (
    <nav className={styles.nav} aria-labelledby="legal-nav-title">
      <h2 id="legal-nav-title" className={styles.navTitle}>
        {t('nav')}
      </h2>
      <ul className={styles.navList}>
        {LEGAL_PAGES.map((other) => (
          <li key={other}>
            <a
              className={styles.navLink}
              href={legalPath(other, locale)}
              aria-current={other === page ? 'page' : undefined}
            >
              {t(`documents.${LEGAL_PAGE_KINDS[other]}`)}
            </a>
          </li>
        ))}
        <li>
          <a className={styles.navLink} href={feesPath(locale)}>
            {tFees('title')}
          </a>
        </li>
        <li>
          <a className={styles.navLink} href={rankingPath(locale)}>
            {tRanking('title')}
          </a>
        </li>
      </ul>
    </nav>
  );
}

/**
 * Links to the six legal pages, the price list and the offer ranking at the bottom of every
 * page (plan: linked in the footer; ZZP čl. 7(9) for the price list, BEAV-191).
 */
export function LegalFooter({ locale }: { locale: Locale }) {
  const t = useTranslations('legal');
  const tFees = useTranslations('fees');
  const tRanking = useTranslations('ranking');
  return (
    <footer className={styles.footer}>
      <nav aria-label={t('footer')}>
        <ul className={styles.footerList} data-testid="legal-footer">
          {LEGAL_PAGES.map((page) => (
            <li key={page}>
              <a href={legalPath(page, locale)}>{t(`documents.${LEGAL_PAGE_KINDS[page]}`)}</a>
            </li>
          ))}
          <li>
            <a href={feesPath(locale)} data-testid="footer-fees">
              {tFees('title')}
            </a>
          </li>
          <li>
            <a href={rankingPath(locale)} data-testid="footer-ranking">
              {tRanking('title')}
            </a>
          </li>
          <li>
            <CookieSettingsButton />
          </li>
        </ul>
      </nav>
    </footer>
  );
}
