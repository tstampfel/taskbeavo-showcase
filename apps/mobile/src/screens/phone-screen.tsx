import { useMutation, useQuery } from '@apollo/client/react';
import {
  RequestPhoneCodeDocument,
  VerifyPhoneCodeDocument,
  ViewerDocument,
  type RequestPhoneCodeMutation,
} from '@taskbeavo/graphql';
import { phonePrefix } from '@taskbeavo/i18n';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, Text } from 'react-native';
import { errorMessage } from '../components/sign-in/error-message';
import { Button, Heading, Input } from '../components/ui';
import { CodeStep, ErrorText, Page, PAGE_HEADING_INSET, styles } from './sign-in-screen';

/** The code just sent, with the number as typed (kept in memory only, to send a new code). */
type Sent = RequestPhoneCodeMutation['requestPhoneCode'] & { phone: string };

export interface PhoneScreenProps {
  /** Verified, skipped with "Not now", or already verified. */
  onDone(): void;
  /** Signed out meanwhile. */
  onSignedOut(): void;
}

/**
 * Verify your phone (mockups 15f and 15g). Posting, offering and chat need a verified mobile
 * number; the API refuses numbers it can't text yet (+1 countries) with
 * PHONE_COUNTRY_NOT_SUPPORTED, shown as its own message. Only the masked number comes back.
 */
export function PhoneScreen({ onDone, onSignedOut }: PhoneScreenProps) {
  const { t } = useTranslation();
  const { data, loading, error, refetch } = useQuery(ViewerDocument, {
    fetchPolicy: 'network-only',
    notifyOnNetworkStatusChange: true,
  });
  const viewer = data?.viewer;
  const [sent, setSent] = useState<Sent | null>(null);
  const signedOut = !loading && data !== undefined && !viewer;

  useEffect(() => {
    if (signedOut) onSignedOut();
  }, [signedOut, onSignedOut]);

  if (!viewer && error && !loading) {
    // Offline or a server error: say so, instead of waiting on a viewer that never comes.
    return (
      <Page
        testID="phone-load-error"
        title={t('phone.header')}
        footer={
          <Button
            label={t('errorBoundary.retry')}
            onPress={() => void refetch().catch(() => undefined)}
            testID="phone-retry"
          />
        }
      >
        <ErrorText error={errorMessage(t, error)} />
      </Page>
    );
  }

  if (!viewer) {
    return (
      <Page testID="phone-loading">
        <ActivityIndicator accessibilityLabel={t('phone.header')} />
      </Page>
    );
  }

  if (viewer.phoneVerified) {
    return (
      <Page
        testID="phone-verified"
        title={t('phone.header')}
        footer={<Button label={t('signIn.verifyCode')} onPress={onDone} testID="phone-continue" />}
      >
        <Text accessibilityRole="alert" style={styles.h1}>
          {t('phone.verified')}
        </Text>
      </Page>
    );
  }

  if (sent) {
    return (
      <PhoneCode
        sent={sent}
        onChange={() => setSent(null)}
        onResent={setSent}
        onVerified={onDone}
      />
    );
  }
  return <PhoneNumber country={viewer.country} onSent={setSent} onLater={onDone} />;
}

function PhoneNumber({
  country,
  onSent,
  onLater,
}: {
  country: string;
  onSent(sent: Sent): void;
  onLater(): void;
}) {
  const { t } = useTranslation();
  const [phone, setPhone] = useState(() => phonePrefix(country));
  const [error, setError] = useState<string | null>(null);
  const [requestPhoneCode, { loading }] = useMutation(RequestPhoneCodeDocument);
  const send = () => {
    setError(null);
    const typed = phone.trim();
    requestPhoneCode({ variables: { phone: typed } }).then(
      ({ data }) => {
        if (data) onSent({ ...data.requestPhoneCode, phone: typed });
      },
      (caught: unknown) => setError(errorMessage(t, caught)),
    );
  };

  return (
    <Page
      testID="phone-number"
      title={t('phone.header')}
      footer={
        <>
          <Button label={t('phone.send')} busy={loading} onPress={send} testID="send-sms" />
          <Button
            label={t('phone.later')}
            variant="ghost"
            disabled={loading}
            onPress={onLater}
            testID="phone-later"
          />
        </>
      }
    >
      <Heading style={styles.h1} inset={PAGE_HEADING_INSET}>
        {t('phone.title')}
      </Heading>
      <Text style={styles.muted}>{t('phone.body')}</Text>
      <Input
        label={t('phone.label')}
        hint={t('phone.hint')}
        error={error ?? undefined}
        keyboardType="phone-pad"
        autoComplete="tel"
        textContentType="telephoneNumber"
        value={phone}
        onChangeText={setPhone}
        onSubmitEditing={send}
        testID="phone-input"
      />
      <Text style={styles.muted}>{t('phone.privacy')}</Text>
    </Page>
  );
}

function PhoneCode({
  sent,
  onChange,
  onResent,
  onVerified,
}: {
  sent: Sent;
  onChange(): void;
  onResent(sent: Sent): void;
  onVerified(): void;
}) {
  const { t } = useTranslation();
  const [verifyPhoneCode] = useMutation(VerifyPhoneCodeDocument);
  const [requestPhoneCode] = useMutation(RequestPhoneCodeDocument);
  const resendAt = new Date(String(sent.resendAvailableAt)).getTime();

  return (
    <CodeStep
      testID="phone-code"
      header={t('phone.header')}
      title={t('phone.codeTitle')}
      intro={t('phone.sentTo', { phone: sent.phoneNumberMasked })}
      changeLabel={t('phone.change')}
      onChange={onChange}
      labels={{
        verify: t('phone.verify'),
        resend: t('phone.resend'),
        resendIn: (time) => t('phone.resendIn', { time }),
      }}
      resendAt={Number.isNaN(resendAt) ? null : resendAt}
      onResend={async () => {
        const { data } = await requestPhoneCode({ variables: { phone: sent.phone } });
        if (data) onResent({ ...data.requestPhoneCode, phone: sent.phone });
      }}
      onSubmit={async (code) => {
        await verifyPhoneCode({ variables: { code } });
        onVerified();
      }}
    />
  );
}
