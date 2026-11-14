'use client';

import { ETA_CHOICES, arrivalStatusMessage, type ArrivalView } from '@taskbeavo/graphql';
import { formatDateTime, formatMoney, formatTime, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useState } from 'react';
import {
  AlertIcon,
  Badge,
  BellIcon,
  Button,
  Card,
  ClockIcon,
  DoorIcon,
  InfoIcon,
  NavIcon,
  Note,
  PinnedFoot,
} from '../ui';
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
  /** The summary card's slot and task title; null while they load. */
  summary: { title: string | null; slotStart: unknown };
}

/**
 * The day of the job (mockup 18) for the side in `view`: the helper's taps and "I was
 * there", the poster's answers to a late report, "Did your helper come?" (a "No" shows the
 * 24-hour warning first) and an early start. Only what the server's `can*` flags allow is
 * offered; every amount is the server's.
 */
export function ArrivalPanel({ view, locale, busy, actions, summary }: ArrivalPanelProps) {
  const t = useTranslations('arrival');
  const time = (at: string | null) => (at ? formatDateTime(at, locale, 'short') : '');
  const helper = view.kind === 'helper';
  // With an ETA the line says when the helper expects to arrive, without one when they set
  // off (BEAV-202); both are clock times on the day of the job.
  const status = arrivalStatusMessage(view);
  const statusTime = status.at ? formatTime(status.at, locale) : '';
  const slot = typeof summary.slotStart === 'string' ? summary.slotStart : null;
  return (
    <>
      <Card className={styles.summary} data-testid="arrival-summary">
        {slot && <p className={styles.caps}>{time(slot)}</p>}
        <p className={styles.strong} role="status" data-testid="arrival-status">
          {helper
            ? t(`statusHelper.${status.key}`, { time: statusTime })
            : t(`statusPoster.${status.key}`, { time: statusTime })}
        </p>
        {summary.title && <p className={`${styles.small} ${styles.muted}`}>{summary.title}</p>}
      </Card>
      {view.noShowRejected && <p className={styles.body}>{t('rejected')}</p>}
      {view.earlyStartConfirmed && <p className={styles.body}>{t('earlyStartConfirmed')}</p>}

      {view.noShow && <NoShowCard view={view} busy={busy} actions={actions} time={time} />}

      {view.kind === 'helper' ? (
        // A fresh part once On my way is recorded: the ETA picked for it isn't a new arrival time.
        <HelperPart key={String(view.canMarkOnMyWay)} view={view} busy={busy} actions={actions} />
      ) : (
        <PosterPart view={view} locale={locale} busy={busy} actions={actions} />
      )}
    </>
  );
}

/** Where a reported no-show stands (18e, 18f), and the helper's "I was there" (18h). */
function NoShowCard({
  view,
  busy,
  actions,
  time,
}: {
  view: ArrivalPanelProps['view'];
  busy: boolean;
  actions: ArrivalActions;
  time(at: string | null): string;
}) {
  const t = useTranslations('arrival');
  const noShow = view.noShow!;
  const helper = view.kind === 'helper';
  const stage = noShow.settled ? 'settled' : noShow.contested ? 'contested' : 'reported';
  const text =
    stage === 'settled'
      ? t(helper ? 'settledHelper' : 'settledPoster')
      : stage === 'contested'
        ? t(helper ? 'contestedHelper' : 'contestedPoster')
        : t(helper ? 'reportedHelper' : 'reportedPoster', { time: time(noShow.contestBy) });
  // The helper's answer window (18h): an orange-edged card with the explanation and the button.
  if (helper && stage === 'reported') {
    return (
      <Card className={`${styles.section} ${styles.alert}`} data-testid="arrival-no-show">
        <p className={styles.iconRow}>
          <span className={styles.orangeIcon}>
            <AlertIcon size={20} />
          </span>
          <span className={styles.body}>{text}</span>
        </p>
        <Card variant="flat" className={styles.flat}>
          <p className={`${styles.small} ${styles.muted}`}>{t('contestExplainer')}</p>
        </Card>
        {view.kind === 'helper' && view.canContestNoShow && (
          <Button disabled={busy} onClick={actions.onContest} data-testid="arrival-contest">
            {t('contest')}
          </Button>
        )}
      </Card>
    );
  }
  return (
    <Card className={styles.section} data-testid="arrival-no-show">
      <Badge tone={stage === 'settled' ? 'grey' : 'orange'}>{t(`noShowBadge.${stage}`)}</Badge>
      <p className={styles.body}>{text}</p>
    </Card>
  );
}

function EtaChoices({
  legend,
  picked,
  onPick,
}: {
  legend: string;
  picked: number | null;
  onPick(m: number): void;
}) {
  const t = useTranslations('arrival');
  return (
    <fieldset className={styles.chips}>
      <legend className={styles.label}>{legend}</legend>
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

/**
 * The helper's taps (18a, then 18g once on the way). On my way and I've arrived, or Send new
 * arrival time, sit in the button bar, pinned only while it fits (PinnedFoot).
 */
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
  // Before setting off the chips are the ETA for On my way; after it (or once the helper
  // says they're late) they're the new arrival time.
  const settingOff = view.canMarkOnMyWay && !late;
  return (
    <>
      {view.canTap && (
        <div className={styles.section}>
          <EtaChoices
            legend={settingOff ? t('etaLegend') : t('runningLate')}
            picked={eta}
            onPick={setEta}
          />
          <Note icon={<BellIcon size={18} />}>{settingOff ? t('onMyWayHint') : t('lateHint')}</Note>
          {settingOff && (
            <Button
              variant="secondary"
              size="sm"
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
            <Card variant="flat" className={styles.flat}>
              <p className={styles.body}>{t('cantComeBody')}</p>
              <Button
                variant="danger"
                disabled={busy}
                onClick={actions.onCantCome}
                data-testid="arrival-cant-come-confirm"
              >
                {t('cantComeConfirm')}
              </Button>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => setCantCome(false)}>
                {t('goBack')}
              </Button>
            </Card>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => setCantCome(true)}
              data-testid="arrival-cant-come"
            >
              {t('cantCome')}
            </Button>
          )}
          <p className={styles.caption}>{t('noReplyHint')}</p>
        </div>
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
      {view.canTap && (
        <PinnedFoot testId="arrival-foot" className={styles.foot}>
          {settingOff ? (
            <Button
              disabled={busy}
              onClick={() => actions.onMyWay(eta)}
              data-testid="arrival-on-my-way"
            >
              <NavIcon size={20} />
              {t('onMyWay')}
            </Button>
          ) : (
            <Button
              disabled={busy || eta === null}
              onClick={() => eta !== null && actions.onLate(eta)}
              data-testid="arrival-send-late"
            >
              {t('sendLate')}
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={actions.onArrived}
            data-testid="arrival-arrived"
          >
            {t('arrived')}
          </Button>
        </PinnedFoot>
      )}
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
          <h2 className={`${styles.h2} ${styles.iconRow}`}>
            <span className={styles.orangeIcon}>
              <ClockIcon size={22} />
            </span>
            {t('lateTitle')}
          </h2>
          <Note icon={<InfoIcon size={18} />}>{t('lateBody')}</Note>
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
        <Card className={`${styles.section} ${styles.hero}`} data-testid="arrival-early-start">
          <span className={styles.greenIcon}>
            <ClockIcon size={48} />
          </span>
          <h2 className={styles.h2}>{t('earlyStartTitle')}</h2>
          <p className={`${styles.body} ${styles.muted}`} data-testid="arrival-early-body">
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
        <Card className={`${styles.section} ${styles.hero}`} data-testid="arrival-did-they-come">
          <span className={styles.orangeIcon}>
            <DoorIcon size={48} />
          </span>
          <h2 className={styles.h2}>{t('cameTitle')}</h2>
          <p className={`${styles.body} ${styles.muted}`}>{t('cameBody')}</p>
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
                <Note
                  tone="warn"
                  icon={<AlertIcon size={18} />}
                  data-testid="arrival-no-show-warning"
                >
                  {t('noShowWarning', { amount: money(offer.refundCents) })}
                  {offer.creditCents > 0 &&
                    ` ${t('noShowCredit', { amount: money(offer.creditCents) })}`}
                </Note>
                <Button
                  variant="danger"
                  disabled={busy}
                  onClick={() => actions.onConfirmCame(false)}
                  data-testid="arrival-no-show-confirm"
                >
                  {t('noShowConfirm')}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => setSayingNo(false)}
                >
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
