import { LinkTargetScreen } from '../../../src/components/link-target-screen';

/**
 * The poster's choice after the helper couldn't get in: reschedule, accept the call-out or
 * dispute. Opening it chooses nothing. Placeholder until the screen is built; links open it
 * (@taskbeavo/app-links).
 */
export default function NoAccess() {
  return <LinkTargetScreen target="noAccess" />;
}
