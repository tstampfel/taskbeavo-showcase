'use client';

import { useMutation, useQuery } from '@apollo/client/react';
import {
  ClearPosterBusinessDocument,
  PosterBusinessDocument,
  SetPosterBusinessDocument,
  signInErrorOf,
  type PosterBusinessKind,
} from '@taskbeavo/graphql';
import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { useRouter } from '../../i18n/navigation';
import { errorMessage } from '../sign-in/error-message';
import styles from './settings.module.css';
import { Button, Card, Input, Sheet } from '../ui';

const KINDS: PosterBusinessKind[] = ['OBRT', 'COMPANY'];

type Translate = ReturnType<typeof useTranslations>;
type Business = { kind: PosterBusinessKind; name: string; oibMasked: string } | null;

/** The OIB codes belong to the OIB field; the poster-specific wording replaces the helper's. */
function oibError(t: Translate, error: unknown): string | null {
  const { code } = signInErrorOf(error);
  if (code === 'INVALID_OIB') return t('businessDetails.invalidOib');
  if (code === 'INVALID_BUSINESS_OIB') return t('businessDetails.invalidObrtOib');
  return null;
}

/**
 * Settings → Business details (BEAV-199): the obrt or company a poster books for, through the
 * BEAV-195 operations. Saving asks first, because a business poster books registered
 * businesses only and private helpers' open offers on their tasks are withdrawn. Only the
 * masked OIB ever comes back, so editing asks for the OIB again. The form follows mockup 14c's
 * business branch, as on mobile (BEAV-215): the kind as radio cards, then the name and OIB.
 */
export function BusinessDetails() {
  const t = useTranslations();
  const router = useRouter();
  const { data, loading, error, refetch } = useQuery(PosterBusinessDocument, {
    fetchPolicy: 'network-only',
    notifyOnNetworkStatusChange: true,
  });
  const viewer = data?.viewer;
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!loading && data && !viewer) {
      router.replace({ pathname: '/sign-in', query: { next: '/settings' } });
    }
  }, [loading, data, viewer, router]);

  if (!viewer && error && !loading) {
    return (
      <section className={styles.section} data-testid="business-load-error">
        <h2 className={styles.heading}>{t('businessDetails.title')}</h2>
        <p className={styles.error} role="alert">
          {errorMessage(t, error)}
        </p>
        <Button
          fullWidth
          onClick={() => void refetch().catch(() => undefined)}
          data-testid="business-retry"
        >
          {t('errorBoundary.retry')}
        </Button>
      </section>
    );
  }

  if (!viewer) return <p role="status" aria-busy="true" className={styles.muted} />;

  const business = viewer.posterBusiness ?? null;
  if (editing) {
    return (
      <BusinessForm
        business={business}
        onCancel={() => setEditing(false)}
        onSaved={() => {
          setEditing(false);
          setNotice(t('businessDetails.saved'));
        }}
      />
    );
  }
  return (
    <BusinessSummary
      business={business}
      notice={notice}
      onEdit={() => {
        setNotice(null);
        setEditing(true);
      }}
      onRemoved={() => setNotice(t('businessDetails.removed'))}
    />
  );
}

function BusinessSummary({
  business,
  notice,
  onEdit,
  onRemoved,
}: {
  business: Business;
  notice: string | null;
  onEdit(): void;
  onRemoved(): void;
}) {
  const t = useTranslations();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clear, { loading }] = useMutation(ClearPosterBusinessDocument);
  const remove = () => {
    setConfirming(false);
    setError(null);
    clear().then(onRemoved, (caught: unknown) => setError(errorMessage(t, caught)));
  };

  return (
    <section className={styles.section} data-testid="business-details">
      <h2 className={styles.heading}>{t('businessDetails.title')}</h2>
      {notice ? (
        <p className={styles.note} role="status" data-testid="business-notice">
          {notice}
        </p>
      ) : null}
      <p className={styles.muted}>{t('businessDetails.intro')}</p>
      {business ? (
        <Card data-testid="business-saved">
          <p className={styles.name}>{business.name}</p>
          <p className={styles.text}>{t(`businessDetails.kind.${business.kind}`)}</p>
          <p className={styles.text} data-testid="business-oib">
            {t('businessDetails.savedOib', { oib: business.oibMasked })}
          </p>
          <div className={styles.actions}>
            <Button
              variant="secondary"
              size="sm"
              disabled={loading}
              onClick={onEdit}
              data-testid="business-edit"
            >
              {t('businessDetails.edit')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={loading}
              onClick={() => setConfirming(true)}
              data-testid="business-remove"
            >
              {t('businessDetails.remove')}
            </Button>
          </div>
        </Card>
      ) : (
        <>
          <p className={styles.text} data-testid="business-none">
            {t('businessDetails.privatePoster')}
          </p>
          <Button fullWidth onClick={onEdit} data-testid="business-add">
            {t('businessDetails.add')}
          </Button>
        </>
      )}
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
      <Sheet
        open={confirming}
        onClose={() => setConfirming(false)}
        title={t('businessDetails.removeTitle')}
        closeLabel={t('businessDetails.cancel')}
      >
        <p className={styles.text}>{t('businessDetails.removeBody')}</p>
        <div className={styles.actions}>
          <Button fullWidth onClick={remove} data-testid="business-remove-confirm">
            {t('businessDetails.confirmRemove')}
          </Button>
          <Button
            variant="ghost"
            fullWidth
            onClick={() => setConfirming(false)}
            data-testid="business-remove-cancel"
          >
            {t('businessDetails.cancel')}
          </Button>
        </div>
      </Sheet>
    </section>
  );
}

function BusinessForm({
  business,
  onCancel,
  onSaved,
}: {
  business: Business;
  onCancel(): void;
  onSaved(): void;
}) {
  const t = useTranslations();
  const [kind, setKind] = useState<PosterBusinessKind>(business?.kind ?? 'OBRT');
  const [name, setName] = useState(business?.name ?? '');
  // Never prefilled: the API only returns the masked OIB.
  const [oib, setOib] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [save, { loading }] = useMutation(SetPosterBusinessDocument);

  const submit = () => {
    setConfirming(false);
    setError(null);
    setFieldError(null);
    save({ variables: { input: { kind, name: name.trim(), oib: oib.trim() } } }).then(
      () => {
        setOib('');
        onSaved();
      },
      (caught: unknown) => {
        const onField = oibError(t, caught);
        if (onField) setFieldError(onField);
        else setError(errorMessage(t, caught));
      },
    );
  };

  return (
    <form
      className={styles.section}
      data-testid="business-form"
      onSubmit={(event) => {
        event.preventDefault();
        setConfirming(true);
      }}
    >
      <h2 className={styles.heading}>{t('businessDetails.title')}</h2>
      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>{t('businessDetails.kindLabel')}</legend>
        <div className={styles.stack}>
          {KINDS.map((option) => (
            <label key={option} className={styles.kind}>
              <input
                type="radio"
                name="kind"
                value={option}
                checked={kind === option}
                onChange={() => {
                  setKind(option);
                  setFieldError(null);
                }}
                data-testid={`business-kind-${option}`}
              />
              <span>{t(`businessDetails.kind.${option}`)}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <Input
        label={t('businessDetails.nameLabel')}
        hint={t('businessDetails.nameHint')}
        autoComplete="organization"
        maxLength={200}
        value={name}
        onChange={(event) => setName(event.target.value)}
        data-testid="business-name"
      />
      <Input
        label={t('businessDetails.oibLabel')}
        hint={t(kind === 'OBRT' ? 'businessDetails.oibHintObrt' : 'businessDetails.oibHintCompany')}
        error={fieldError ?? undefined}
        inputMode="numeric"
        autoComplete="off"
        maxLength={11}
        value={oib}
        onChange={(event) => {
          setOib(event.target.value);
          setFieldError(null);
        }}
        data-testid="business-oib-input"
      />
      {business ? <p className={styles.muted}>{t('businessDetails.oibReenter')}</p> : null}
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
      <div className={styles.actions}>
        <Button type="submit" fullWidth disabled={loading} data-testid="business-save">
          {t('businessDetails.save')}
        </Button>
        <Button
          variant="ghost"
          fullWidth
          disabled={loading}
          onClick={onCancel}
          data-testid="business-cancel"
        >
          {t('businessDetails.cancel')}
        </Button>
      </div>
      <Sheet
        open={confirming}
        onClose={() => setConfirming(false)}
        title={t('businessDetails.confirmTitle')}
        closeLabel={t('businessDetails.cancel')}
      >
        <p className={styles.text}>{t('businessDetails.confirmBody')}</p>
        <div className={styles.actions}>
          <Button fullWidth onClick={submit} data-testid="business-confirm">
            {t('businessDetails.confirmSave')}
          </Button>
          <Button
            variant="ghost"
            fullWidth
            onClick={() => setConfirming(false)}
            data-testid="business-confirm-cancel"
          >
            {t('businessDetails.cancel')}
          </Button>
        </div>
      </Sheet>
    </form>
  );
}
