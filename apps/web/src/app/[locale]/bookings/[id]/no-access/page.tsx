import { linkTargetPage } from '../../../../../components/link-target-view';

/** The poster's choice after the helper couldn't get in. Placeholder until the page is built. */
const { Page, generateMetadata } = linkTargetPage('noAccess', (id) => `/bookings/${id}`);

export { generateMetadata };
export default Page;
