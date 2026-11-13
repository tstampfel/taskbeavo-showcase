import { legalRoute } from '../../../components/legal/legal-route';

/** /{locale}/privacy: the version in force (BEAV-81, mockup 21e). */
const { Page, generateMetadata } = legalRoute('privacy');

export { generateMetadata };
export default Page;
