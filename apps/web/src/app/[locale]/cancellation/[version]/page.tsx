import { legalRoute } from '../../../../components/legal/legal-route';

/** /{locale}/cancellation/{version}: an earlier or scheduled version. */
const { Page, generateMetadata } = legalRoute('cancellation');

export { generateMetadata };
export default Page;
