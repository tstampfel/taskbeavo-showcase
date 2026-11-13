import { legalRoute } from '../../../components/legal/legal-route';

/** /{locale}/terms: the version in force (BEAV-81, mockup 21e). */
const { Page, generateMetadata } = legalRoute('terms');

export { generateMetadata };
export default Page;
