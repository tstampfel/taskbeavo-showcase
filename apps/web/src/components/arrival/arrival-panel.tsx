'use client';

import { ETA_CHOICES, type ArrivalView } from '@taskbeavo/graphql';
import { formatDateTime, formatMoney, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import { Button, Card } from '../ui';
import styles from './arrival.module.css';

export interface ArrivalActions {
  onMyWay(etaMinutes: number | null): void;
  onLate(etaMinutes: number): void;
  onArrived(): void;
  onCantCome(): void;
  onStartEarly(): void;
  onContest(): void;
  onAnswerLate(keep: boolean): void;
  onConfirmCame(came: boolean): void;
  onAnswerEarly(confirm: boolean): void;
}

export interface ArrivalPanelProps {
  view: Exclude<ArrivalView, { kind: 'unknownViewer' }>;
  locale: Locale;
  busy: boolean;
  actions: ArrivalActions;
}

/**
 * The day of the job (mockup 18) for the side in `view`: the helper's taps and "I was
 * there", the poster's answers to a late report, "Did your helper come?" (a "No" shows the
 * 24-hour warning first) and an early start. Only what the server's `can*` flags allow is
 * offered; every amount is the server's.
 */
export function ArrivalPanel({ view, locale, busy, actions }: ArrivalPanelProps) {
  const t = useTranslations('arrival');
  const time = (at: string | null) => (at ? formatDateTime(at, locale, 'short') : '');
  const helper = view.kind === 'helper';
  return (
    <>
      <p className={styles.body} role="status" data-testid="arrival-status">
        {helper
          ? t(`statusHelper.${view.status}`, { time: time(view.etaAt) })
          : t(`statusPoster.${view.status}`, { time: time(view.etaAt) })}
      </p>
      {view.noShowRejected && <p className={styles.body}>{t('rejected')}</p>}
      {view.earlyStartConfirmed && <p className={styles.body}>{t('earlyStartConfirmed')}</p>}

      {view.noShow && (
        <Card className={styles.section} data-testid="arrival-no-show">
          <p className={styles.body}>
            {view.noShow.settled
              ? t(helper ? 'settledHelper' : 'settledPoster')
              : view.noShow.contested
                ? t(helper ? 'contestedHelper' : 'contestedPoster')
                : t(helper ? 'reportedHelper' : 'reportedPoster', {
                    time: time(view.noShow.contestBy),
                  })}
          </p>
          {helper && view.canContestNoShow && (
            <Button disabled={busy} onClick={actions.onContest} data-testid="arrival-contest">
              {t('contest')}
            </Button>
          )}
        </Card>
      )}

      {view.kind === 'helper' ? (
        <HelperPart view={view} busy={busy} actions={actions} />
      ) : (
        <PosterPart view={view} locale={locale} busy={busy} actions={actions} />
      )}
    </>
  );
}

function EtaChoices({ picked, onPick }: { picked: number | null; onPick(m: number): void }) {
  const t = useTranslations('arrival');
  return (
    <fieldset className={styles.chips}>
      <legend className={`${styles.small} ${styles.muted}`}>{t('etaLegend')}</legend>
      {ETA_CHOICES.map((minutes) => (
        <label key={minutes} className={styles.chip}>
          <input
            type="radio"
            name="eta"
            checked={picked === minutes}
            onChange={() => onPick(minutes)}
            data-testid={`arrival-eta-${minutes}`}
          />
          {t('etaMinutes', { minutes })}
        </label>
      ))}
    </fieldset>
  );
}

function HelperPart({
  view,
  busy,
  actions,
}: {
  view: Extract<ArrivalView, { kind: 'helper' }>;
  busy: boolean;
  actions: ArrivalActions;
}) {
  const t = useTranslations('arrival');
  const [eta, setEta] = useState<number | null>(null);
  const [late, setLate] = useState(false);
  const [cantCome, setCantCome] = useState(false);
  return (
    <>
      {view.canTap && (
        <Card className={styles.section}>
          {(view.canMarkOnMyWay || late) && <EtaChoices picked={eta} onPick={setEta} />}
          {view.canMarkOnMyWay && (
            <>
              <p className={`${styles.small} ${styles.muted}`}>{t('onMyWayHint')}</p>
              <Button
                disabled={busy}
                onClick={() => actions.onMyWay(eta)}
                data-testid="arrival-on-my-way"
              >
                {t('onMyWay')}
              </Button>
            </>
          )}
          <Button
            variant="secondary"
            disabled={busy}
            onClick={actions.onArrived}
            data-testid="arrival-arrived"
          >
            {t('arrived')}
          </Button>
          {late ? (
            <Button
              variant="secondary"
              disabled={busy || eta === null}
              onClick={() => eta !== null && actions.onLate(eta)}
              data-testid="arrival-send-late"
            >
              {t('sendLate')}
            </Button>
          ) : (
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => {
                setEta(null);
                setLate(true);
              }}
              data-testid="arrival-running-late"
            >
              {t('runningLate')}
            </Button>
          )}
          {cantCome ? (
            <div className={styles.section}>
              <p className={styles.body}>{t('cantComeBody')}</p>
              <Button
                variant="danger"
                disabled={busy}
                onClick={actions.onCantCome}
                data-testid="arrival-cant-come-confirm"
              >
                {t('cantComeConfirm')}
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => setCantCome(false)}>
                {t('goBack')}
              </Button>
            </div>
          ) : (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => setCantCome(true)}
              data-testid="arrival-cant-come"
            >
              {t('cantCome')}
            </Button>
          )}
          <p className={`${styles.small} ${styles.muted}`}>{t('noReplyHint')}</p>
        </Card>
      )}
      {view.canRequestEarlyStart && (
        <Card className={styles.section}>
          <p className={`${styles.small} ${styles.muted}`}>{t('startNowHint')}</p>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={actions.onStartEarly}
            data-testid="arrival-start-now"
          >
            {t('startNow')}
          </Button>
        </Card>
      )}
      {view.earlyStartPending && <p className={styles.body}>{t('earlyStartWaiting')}</p>}
    </>
  );
}

function PosterPart({
  view,
  locale,
  busy,
  actions,
}: {
  view: Extract<ArrivalView, { kind: 'poster' }>;
  locale: Locale;
  busy: boolean;
  actions: ArrivalActions;
}) {
  const t = useTranslations('arrival');
  const [sayingNo, setSayingNo] = useState(false);
  const money = (cents: number) => formatMoney(cents, locale);
  const offer = view.noShowOffer;
  return (
    <>
      {view.canAnswerLate && (
        <Card className={styles.section} data-testid="arrival-late">
          <h2 className={styles.h2}>{t('lateTitle')}</h2>
          <p className={styles.body}>{t('lateBody')}</p>
          <Button
            disabled={busy}
            onClick={() => actions.onAnswerLate(true)}
            data-testid="arrival-late-keep"
          >
            {t('lateKeep')}
          </Button>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => actions.onAnswerLate(false)}
            data-testid="arrival-late-cancel"
          >
            {t('lateCancel')}
          </Button>
        </Card>
      )}

      {view.canAnswerEarlyStart && (
        <Card className={styles.section} data-testid="arrival-early-start">
          <h2 className={styles.h2}>{t('earlyStartTitle')}</h2>
          <p className={styles.body} data-testid="arrival-early-body">
            {t(view.earlyStartWithdrawal ? 'earlyStartBodyWithdrawal' : 'earlyStartBody')}
          </p>
          <Button
            disabled={busy}
            onClick={() => actions.onAnswerEarly(true)}
            data-testid="arrival-early-confirm"
          >
            {t('earlyStartConfirm')}
          </Button>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => actions.onAnswerEarly(false)}
            data-testid="arrival-early-decline"
          >
            {t('earlyStartDecline')}
          </Button>
        </Card>
      )}

      {(view.canConfirmCame || offer) && (
        <Card className={styles.section} data-testid="arrival-did-they-come">
          <h2 className={styles.h2}>{t('cameTitle')}</h2>
          <p className={styles.body}>{t('cameBody')}</p>
          {view.canConfirmCame && (
            <Button
              disabled={busy}
              onClick={() => actions.onConfirmCame(true)}
              data-testid="arrival-came-yes"
            >
              {t('cameYes')}
            </Button>
          )}
          {offer &&
            (sayingNo ? (
              <div className={styles.section}>
                <p className={styles.body} data-testid="arrival-no-show-warning">
                  {t('noShowWarning', { amount: money(offer.refundCents) })}
                </p>
                {offer.creditCents > 0 && (
                  <p className={styles.body}>
                    {t('noShowCredit', { amount: money(offer.creditCents) })}
                  </p>
                )}
                <Button
                  variant="danger"
                  disabled={busy}
                  onClick={() => actions.onConfirmCame(false)}
                  data-testid="arrival-no-show-confirm"
                >
                  {t('noShowConfirm')}
                </Button>
                <Button variant="ghost" disabled={busy} onClick={() => setSayingNo(false)}>
                  {t('goBack')}
                </Button>
              </div>
            ) : (
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => setSayingNo(true)}
                data-testid="arrival-came-no"
              >
                {t('cameNo')}
              </Button>
            ))}
        </Card>
      )}
    </>
  );
}
