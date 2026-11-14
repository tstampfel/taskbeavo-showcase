import { linkTargetPage } from '../../../../../components/link-target-view';

/** "Release payment": opening it releases nothing; the poster taps to release. Placeholder until the page is built. */
const { Page, generateMetadata } = linkTargetPage('release', (id) => `/bookings/${id}`);

export { generateMetadata };
export default Page;
