import { LinkTargetScreen } from '../../../src/components/link-target-screen';

/**
 * The helper's Stripe payout account: status and what Stripe still needs. Placeholder until the
 * screen is built (BEAV-97); links and the "more info needed" push open it (@taskbeavo/app-links).
 */
export default function Payouts() {
  return <LinkTargetScreen target="payouts" />;
}
