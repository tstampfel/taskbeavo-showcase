import { legalRoute } from '../../../components/legal/legal-route';

/** /{locale}/cancellation: the version in force (BEAV-81, mockup 21e). */
const { Page, generateMetadata } = legalRoute('cancellation');

export { generateMetadata };
export default Page;
