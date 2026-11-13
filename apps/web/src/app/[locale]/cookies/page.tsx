import { legalRoute } from '../../../components/legal/legal-route';

/** /{locale}/cookies: the version in force (BEAV-81, mockup 21e). */
const { Page, generateMetadata } = legalRoute('cookies');

export { generateMetadata };
export default Page;
