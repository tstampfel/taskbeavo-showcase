import { useMutation, useQuery } from '@apollo/client/react';
import {
  BookingDetailDocument,
  PAYMENT_REQUEST_MAX_PHOTOS,
  RequestPaymentDocument,
  apiErrorCode,
  paymentRequestInput,
  type PaymentRequestProblem,
} from '@taskbeavo/graphql';
import { defaultLocale, formatMoney, isLocale, type Locale } from '@taskbeavo/i18n';
import { colors, spacing } from '@taskbeavo/ui-tokens';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { AccessibilityInfo, ActivityIndicator, StyleSheet } from 'react-native';
import {
  AmountRow,
  AmountRowGroup,
  Button,
  Card,
  Heading,
  Input,
  Note,
  Screen,
} from '../components/ui';
import { Text } from '../components/ui/text';
import { textStyle } from '../components/ui/text-style';
import { PhotoUploader } from '../uploads/photo-uploader';

export interface RequestPaymentScreenProps {
  bookingId: string;
  /** Back to the booking: after sending, or from the top bar. */
  onBack(): void;
}

/**
 * The helper's Request payment (BEAV-116, mockup 7a): photos of the finished job and a note,
 * both optional, and for a registered business its fiscalized invoice first, as a photo or
 * scan or by its JIR. Sending moves no money: the poster is told and releases. Photos are
 * taken with the camera (evidence, re-encoded without metadata on the server). The agreed
 * price sits on top, as in mockup 7a (BEAV-118).
 */
export function RequestPaymentScreen({ bookingId, onBack }: RequestPaymentScreenProps) {
  const { t, i18n } = useTranslation();
  const locale: Locale = isLocale(i18n.language) ? i18n.language : defaultLocale;
  const booking = useQuery(BookingDetailDocument, {
    variables: { id: bookingId },
    fetchPolicy: 'cache-and-network',
  });
  const [requestPayment, sending] = useMutation(RequestPaymentDocument);
  const [photoIds, setPhotoIds] = useState<string[]>([]);
  const [invoiceUploadId, setInvoiceUploadId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [jir, setJir] = useState('');
  const [problem, setProblem] = useState<PaymentRequestProblem | null>(null);
  const current = booking.data?.booking;

  const frame = (children: ReactNode, footer?: ReactNode) => (
    <Screen
      title={t('requestPayment.title')}
      onBack={onBack}
      backLabel={t('requestPayment.back')}
      footer={footer}
      testID="request-payment-screen"
    >
      {children}
    </Screen>
  );

  if (!current) {
    return frame(
      booking.loading ? (
        <ActivityIndicator color={colors.green} accessibilityLabel={t('requestPayment.title')} />
      ) : (
        <>
          <Text accessibilityRole="alert" style={[textStyle('body'), styles.ink]}>
            {t('requestPayment.loadError')}
          </Text>
          <Button
            variant="secondary"
            label={t('requestPayment.retry')}
            onPress={() => void booking.refetch().catch(() => undefined)}
          />
        </>
      ),
    );
  }

  if (current.status === 'PAYMENT_REQUESTED') {
    return frame(
      <Card testID="payment-requested">
        <Heading style={[textStyle('h2'), styles.ink]}>{t('requestPayment.requested')}</Heading>
        <Text style={[textStyle('body'), styles.ink]}>
          {t('requestPayment.requestedBody', {
            amount: formatMoney(current.amountCents, locale),
          })}
        </Text>
      </Card>,
      <Button variant="secondary" label={t('requestPayment.backToBooking')} onPress={onBack} />,
    );
  }

  if (!current.canRequestPayment) {
    return frame(
      <Note testID="request-payment-unavailable">{t('requestPayment.unavailable')}</Note>,
    );
  }

  const errorCode = sending.error ? apiErrorCode(sending.error) : undefined;
  const send = () => {
    const check = paymentRequestInput(
      bookingId,
      { note, photoIds, invoiceUploadId, invoiceJir: jir },
      current.invoiceRequired,
    );
    if (!check.ok) {
      setProblem(check.problem);
      return;
    }
    setProblem(null);
    void requestPayment({ variables: { input: check.input } })
      .then(() => {
        AccessibilityInfo.announceForAccessibility(t('requestPayment.requested'));
      })
      .catch(() => undefined);
  };

  return frame(
    <>
      <Card testID="request-payment-summary">
        <Heading style={[textStyle('h2'), styles.ink]}>{current.task.title}</Heading>
        <AmountRowGroup>
          <AmountRow
            label={t('requestPayment.agreedPrice')}
            value={formatMoney(current.amountCents, locale)}
            style={[textStyle('label'), styles.ink]}
            testID="request-payment-price"
          />
        </AmountRowGroup>
      </Card>
      {current.invoiceRequired && (
        <Card testID="request-payment-invoice">
          <Heading style={[textStyle('label'), styles.ink]}>
            {t('requestPayment.invoiceTitle')}
          </Heading>
          <Text style={[textStyle('body'), styles.ink]}>{t('requestPayment.invoiceBody')}</Text>
          {invoiceUploadId ? (
            <Text style={[textStyle('body'), styles.ink]} testID="invoice-added">
              {t('requestPayment.invoiceAdded')}
            </Text>
          ) : (
            <PhotoUploader
              purpose="INVOICE"
              onUploaded={(upload) => setInvoiceUploadId(upload.id)}
            />
          )}
          {!invoiceUploadId && (
            <Input
              label={t('requestPayment.jirLabel')}
              hint={t('requestPayment.jirHint')}
              value={jir}
              onChangeText={setJir}
              autoCapitalize="none"
              autoCorrect={false}
              error={
                problem === 'jirInvalid' || problem === 'invoiceMissing'
                  ? t(`requestPayment.problems.${problem}`)
                  : undefined
              }
              testID="request-payment-jir"
            />
          )}
        </Card>
      )}
      <Card testID="request-payment-photos">
        <Heading style={[textStyle('label'), styles.ink]}>
          {t('requestPayment.photosTitle')}
        </Heading>
        {photoIds.length > 0 && (
          <Text style={[textStyle('body'), styles.ink]} testID="photos-added">
            {t('requestPayment.photosAdded', { count: photoIds.length })}
          </Text>
        )}
        {photoIds.length < PAYMENT_REQUEST_MAX_PHOTOS ? (
          <PhotoUploader
            purpose="EVIDENCE_PHOTO"
            onUploaded={(upload) => setPhotoIds((ids) => [...ids, upload.id])}
          />
        ) : (
          <Text style={[textStyle('small'), styles.ink]}>{t('requestPayment.photosMax')}</Text>
        )}
      </Card>
      <Input
        label={t('requestPayment.noteLabel')}
        hint={t('requestPayment.noteHint')}
        value={note}
        onChangeText={setNote}
        multiline
        error={problem === 'noteTooLong' ? t('requestPayment.problems.noteTooLong') : undefined}
        testID="request-payment-note"
      />
      <Text style={[textStyle('small'), styles.ink]}>{t('requestPayment.releaseNote')}</Text>
      {problem === 'tooManyPhotos' && (
        <Text accessibilityRole="alert" style={[textStyle('body'), styles.error]}>
          {t('requestPayment.problems.tooManyPhotos')}
        </Text>
      )}
      {errorCode && (
        <Text
          accessibilityRole="alert"
          style={[textStyle('body'), styles.error]}
          testID="request-payment-error"
        >
          {i18n.exists(`errors.${errorCode}`)
            ? t(`errors.${errorCode}`)
            : t('requestPayment.loadError')}
        </Text>
      )}
    </>,
    <Button
      label={t('requestPayment.submit')}
      busy={sending.loading}
      disabled={sending.loading}
      onPress={send}
      testID="request-payment-submit"
    />,
  );
}

const styles = StyleSheet.create({
  ink: { color: colors.ink },
  error: { color: colors.error, marginTop: spacing.sm },
});
