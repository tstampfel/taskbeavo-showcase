import { linkTargetPage } from '../../../../../components/link-target-view';

/**
 * Where Stripe sends the helper when an onboarding link expired or was used; the real page asks
 * for a new one (startPayoutOnboarding). Placeholder until the page is built.
 */
const { Page, generateMetadata } = linkTargetPage('payouts', () => '/helper/payouts');

export { generateMetadata };
export default Page;
