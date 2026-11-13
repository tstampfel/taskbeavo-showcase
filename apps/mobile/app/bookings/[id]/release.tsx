import { LinkTargetScreen } from '../../../src/components/link-target-screen';

/**
 * "Release payment" confirmation. Opening it never releases anything: the poster taps to
 * release and the server checks the booking. Placeholder until the screen is built; links open
 * it (@taskbeavo/app-links).
 */
export default function ReleasePayment() {
  return <LinkTargetScreen target="release" />;
}
