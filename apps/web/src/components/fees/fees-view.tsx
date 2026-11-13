import { feesPath, legalPath, priceListCsvPath, rankingPath } from '@taskbeavo/app-links';
import type {
  CancellationAmountsFieldsFragment,
  ServiceFeeScheduleFieldsFragment,
} from '@taskbeavo/graphql';
import { formatDate, formatMoney, formatPercent, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import styles from '../legal/legal.module.css';
import fees from './fees.module.css';
import { dayBefore } from './price-list';

export type FeeSchedule = ServiceFeeScheduleFieldsFragment;
export type CancellationAmounts = CancellationAmountsFieldsFragment;

/** "12% of the task price, at least €2.50", from the API's numbers. */
function FeeRule({ schedule, locale }: { schedule: FeeSchedule; locale: Locale }) {
  const t = useTranslations('fees');
  return t('feeRule', {
    percent: formatPercent(schedule.percent, locale),
    minimum: formatMoney(schedule.minimumCents, locale),
  });
}

function DraftNote() {
  const t = useTranslations('fees');
  return (
    <p className={styles.note} role="note" data-testid="fees-draft">
      <strong>{t('draftTitle')}</strong> <span>{t('draftBody')}</span>
    </p>
  );
}

/**
 * Variant A or B, from FEE_VAT (backend/bookings/money.py); until it is set, neither. Variant B
 * ends with the statutory note (čl. 90 st. 1, BEAV-194), the same sentence the receipts print
 * (backend/vat/wording.py).
 */
function Vat({ schedule, locale }: { schedule: FeeSchedule; locale: Locale }) {
  const t = useTranslations('fees');
  const example = schedule.examples.find((row) => row.vatCents != null);
  switch (schedule.vat) {
    case 'INCLUDED':
      return t('vatIncluded', {
        vatPercent: formatPercent(schedule.vatPercent, locale),
        vat: formatMoney(example?.vatCents ?? 0, locale),
        fee: formatMoney(example?.feeCents ?? 0, locale),
      });
    case 'NOT_CHARGED':
      return t('vatNotCharged');
    default:
      return t('vatUndecided');
  }
}

function RefundTable({ amounts, locale }: { amounts: CancellationAmounts; locale: Locale }) {
  const t = useTranslations('fees.refund');
  const money = (cents: number) => formatMoney(cents, locale);
  const percent = (value: number) => formatPercent(value, locale);
  const kept = percent(amounts.keptFeePercent);
  const refunded = t('refunded');
  // [key, situation, task price, fee]; a null fee means the note spans both columns.
  const rows: [string, string, string, string | null][] = [
    ['before-accept', t('beforeAccept'), t('beforeAcceptNote'), null],
    [
      'early',
      t('early', {
        graceHours: amounts.gracePeriodMinutes / 60,
        lateHours: amounts.lateWindowHours,
      }),
      refunded,
      refunded,
    ],
    [
      'late',
      t('late', { lateHours: amounts.lateWindowHours, days: amounts.withdrawalPeriodDays }),
      t('latePrice', {
        percent: percent(amounts.latePayPercent),
        min: money(amounts.latePayMinCents),
        max: money(amounts.latePayMaxCents),
      }),
      t('lateFee', { percent: kept }),
    ],
    [
      'withdraw-before',
      t('withdrawBefore', { days: amounts.withdrawalPeriodDays }),
      refunded,
      refunded,
    ],
    [
      'withdraw-after',
      t('withdrawAfter', { days: amounts.withdrawalPeriodDays }),
      t('withdrawAfterPrice'),
      t('withdrawAfterFee', { percent: kept }),
    ],
    [
      'call-out',
      t('callOut'),
      t('callOutPrice', {
        percent: percent(amounts.callOutPercent),
        min: money(amounts.callOutMinCents),
        max: money(amounts.callOutMaxCents),
      }),
      t('callOutFee', { percent: kept }),
    ],
    [
      'helper-cancel',
      t('helperCancel'),
      refunded,
      t('helperCancelFee', {
        credit: money(amounts.helperCancelCreditCents),
        lateHours: amounts.lateWindowHours,
      }),
    ],
    ['no-fault', t('noFault'), refunded, refunded],
    ['work-started', t('workStarted'), t('workStartedNote'), null],
    ['payment-requested', t('paymentRequested'), t('paymentRequestedNote'), null],
  ];
  return (
    <div className={fees.tableWrap}>
      <table className={fees.table} data-testid="fees-refunds">
        <thead>
          <tr>
            <th scope="col">{t('situation')}</th>
            <th scope="col">{t('taskPrice')}</th>
            <th scope="col">{t('fee')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([key, situation, price, fee]) => (
            <tr key={key} data-testid={`fees-refund-${key}`}>
              <th scope="row">{situation}</th>
              {fee === null ? (
                <td colSpan={2}>{price}</td>
              ) : (
                <>
                  <td>{price}</td>
                  <td>{fee}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      <p data-testid="fees-refund-rate">{t('rate')}</p>
    </div>
  );
}

/**
 * /{locale}/fees (BEAV-191, ZZP čl. 7(9)): the service fee price list in force, in the
 * lawyer's draft wording (Drafting Pack v1, item 9). Every amount comes from the API (the
 * schedule checkout charges and the cancellation policy's numbers); the catalogue copy
 * carries none. DRAFT until the lawyer approves it.
 */
export function FeesView({
  schedule,
  upcoming = [],
  cancellation,
  locale,
}: {
  /** The list checkout charges now. */
  schedule: FeeSchedule;
  /** Announced lists, charged from their effective date. */
  upcoming?: FeeSchedule[];
  cancellation: CancellationAmounts;
  locale: Locale;
}) {
  const t = useTranslations('fees');
  const money = (cents: number) => formatMoney(cents, locale);
  const date = (value: unknown) => formatDate(String(value), locale, 'long');
  return (
    <main className={styles.main} data-testid="fees">
      <article className={styles.article}>
        <DraftNote />
        <h1 className={styles.title} data-testid="fees-heading">
          {t('heading', { date: date(schedule.effectiveFrom), version: schedule.version })}
        </h1>
        <div className={styles.body} data-testid="fees-body">
          <p data-testid="fees-rule">
            <strong>{t('serviceFeeTitle')}</strong>{' '}
            {t('serviceFee', {
              percent: formatPercent(schedule.percent, locale),
              minimum: money(schedule.minimumCents),
            })}
          </p>
          <div data-testid="fees-examples">
            <p>{t('exampleLabel')}</p>
            <ul>
              {schedule.examples.map((example) => (
                <li key={example.id}>
                  {t(example.feeCents === schedule.minimumCents ? 'exampleMinimum' : 'example', {
                    price: money(example.taskPriceCents),
                    fee: money(example.feeCents),
                    total: money(example.totalCents),
                  })}
                </li>
              ))}
            </ul>
          </div>

          <p data-testid="fees-vat">
            <strong>{t('vatTitle')}</strong> <Vat schedule={schedule} locale={locale} />
          </p>
          <p>{t('vatTaskPrice')}</p>

          <p>
            <strong>{t('receiptsTitle')}</strong> {t('receipts')}
          </p>

          <h2>{t('refundTitle')}</h2>
          <p>
            {t.rich('refundPolicy', {
              link: (chunks) => (
                <a href={legalPath('cancellation', locale)} data-testid="fees-cancellation">
                  {chunks}
                </a>
              ),
            })}
          </p>
          <RefundTable amounts={cancellation} locale={locale} />

          <p data-testid="fees-changes">
            <strong>{t('changesTitle')}</strong>{' '}
            {t.rich('changes', {
              terms: (chunks) => <a href={legalPath('terms', locale)}>{chunks}</a>,
              archive: (chunks) => (
                <a href={feesPath(locale, true)} data-testid="fees-archive">
                  {chunks}
                </a>
              ),
            })}
          </p>
          <p>
            {t.rich('ranking', {
              link: (chunks) => (
                <a href={rankingPath(locale)} data-testid="fees-ranking">
                  {chunks}
                </a>
              ),
            })}
          </p>
          {upcoming.length > 0 ? (
            <ul className={fees.archiveList} data-testid="fees-upcoming">
              {upcoming.map((next) => (
                <li key={next.id}>
                  <p>
                    <strong>{t('upcomingFrom', { date: date(next.effectiveFrom) })}</strong>{' '}
                    <FeeRule schedule={next} locale={locale} />
                  </p>
                  <a className={styles.link} href={priceListCsvPath(next.version)}>
                    {t('csv')}
                  </a>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <a className={styles.link} href={priceListCsvPath()} data-testid="fees-csv">
          {t('csv')}
        </a>
      </article>
    </main>
  );
}

/**
 * /{locale}/fees/archive: the list in force and every earlier one, newest first, each with
 * the days it applied and its CSV (NN 101/2026 keeps each list available for at least 30
 * days). Announced lists stay off it until they apply.
 */
export function FeesArchiveView({
  schedules,
  locale,
}: {
  schedules: FeeSchedule[];
  locale: Locale;
}) {
  const t = useTranslations('fees');
  const date = (value: string) => formatDate(value, locale, 'long');
  return (
    <main className={styles.main} data-testid="fees-archive-page">
      <article className={styles.article}>
        <DraftNote />
        <h1 className={styles.title}>{t('archive.title')}</h1>
        <p>{t('archive.intro')}</p>
        <ul className={fees.archiveList} data-testid="fees-archive-list">
          {schedules.map((schedule, index) => {
            const from = String(schedule.effectiveFrom);
            const next = schedules[index - 1];
            return (
              <li key={schedule.id}>
                <h2>{schedule.version}</h2>
                <p className={styles.muted}>
                  {next
                    ? t('archive.periodEnded', {
                        from: date(from),
                        to: date(dayBefore(String(next.effectiveFrom))),
                      })
                    : t('archive.period', { from: date(from) })}
                  {schedule.status === 'CURRENT' ? <> · {t('archive.current')}</> : null}
                </p>
                <p>
                  <FeeRule schedule={schedule} locale={locale} />
                </p>
                <a className={styles.link} href={priceListCsvPath(schedule.version)}>
                  {t('csv')}
                </a>
              </li>
            );
          })}
        </ul>
        <a className={styles.link} href={feesPath(locale)}>
          {t('archive.back')}
        </a>
      </article>
    </main>
  );
}
