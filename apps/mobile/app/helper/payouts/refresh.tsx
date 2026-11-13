import { LinkTargetScreen } from '../../../src/components/link-target-screen';

/**
 * Where Stripe sends the helper when an onboarding link expired or was used; the real screen
 * asks for a new one (startPayoutOnboarding). Placeholder until the screen is built.
 */
export default function PayoutsRefresh() {
  return <LinkTargetScreen target="payouts" />;
}
