import { colors, spacing } from '@taskbeavo/ui-tokens';
import { useRouter } from 'expo-router';
import { lazy, Suspense, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet } from 'react-native';
import { Text } from '../components/ui/text';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Heading } from '../components/ui';
import { textStyle } from '../components/ui/text-style';
import { openFees, openLegal } from '../auth/app-auth';
import { CheckoutScreen } from '../screens/checkout-screen';
import { demoPaymentSheet, demoPaymentsEnabled } from './demo-sheet';
import type * as StripeModule from './stripe';
import { stripePublishableKey, type PaymentSheet } from './stripe-config';

// Expo Router loads every route file at launch, and importing the Stripe SDK starts it (fraud
// signals to m.stripe.com, Stripe's `m` cookie). It loads only when a checkout screen renders
// (BEAV-190). A require inside the loader, not import(): it runs only then, and Jest can run it.
const StripeCheckout = lazy(async () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const stripe: typeof StripeModule = require('./stripe');
  return stripe;
});

interface CheckoutRouteProps {
  taskId?: string;
  offerId?: string;
  bookingId?: string;
  /** acceptOffer opened a checkout: the route can move to the booking's own URL. */
  onPaymentStarted?(bookingId: string): void;
}

/** Checkout with Stripe's native sheet; says so plainly when this build has no Stripe key. */
export function CheckoutRoute(props: CheckoutRouteProps) {
  if (demoPaymentsEnabled()) return <DemoCheckout {...props} />;
  const key = stripePublishableKey();
  if (!key) return <NotConfigured />;
  return (
    <Suspense fallback={<Loading />}>
      <StripeCheckout publishableKey={key}>
        {(sheet) => <WithSheet {...props} sheet={sheet} />}
      </StripeCheckout>
    </Suspense>
  );
}

/** Demo payments: the demo dialog instead of Stripe's sheet; the Stripe SDK never loads. */
function DemoCheckout(props: CheckoutRouteProps) {
  const { t } = useTranslation();
  const sheet = useMemo(() => demoPaymentSheet(t), [t]);
  return <WithSheet {...props} sheet={sheet} />;
}

function WithSheet({
  taskId,
  offerId,
  bookingId,
  sheet,
}: CheckoutRouteProps & { sheet: PaymentSheet }) {
  const router = useRouter();
  return (
    <CheckoutScreen
      taskId={taskId}
      offerId={offerId}
      bookingId={bookingId}
      sheet={sheet}
      onBackToOffers={(task) =>
        router.replace({ pathname: '/tasks/[id]/offers', params: { id: task } })
      }
      onViewBooking={(id) => router.replace({ pathname: '/bookings/[id]', params: { id } })}
      openLegal={openLegal}
      openFees={openFees}
    />
  );
}

function Loading() {
  const { t } = useTranslation();
  return (
    <SafeAreaView style={styles.safeArea} testID="checkout-loading">
      <ActivityIndicator color={colors.green} accessibilityLabel={t('checkout.title')} />
    </SafeAreaView>
  );
}

function NotConfigured() {
  const { t } = useTranslation();
  return (
    <SafeAreaView style={styles.safeArea} testID="checkout-not-configured">
      <Heading style={[textStyle('h1'), styles.title]}>{t('checkout.title')}</Heading>
      <Text accessibilityRole="alert" style={[textStyle('body'), styles.ink]}>
        {t('checkout.notConfigured')}
      </Text>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, gap: spacing.lg, padding: spacing.xl, backgroundColor: colors.offWhite },
  title: { color: colors.green },
  ink: { color: colors.ink },
});
