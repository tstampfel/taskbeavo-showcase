import { legalRoute } from '../../../components/legal/legal-route';

/** /{locale}/imprint: the version in force (BEAV-81, mockup 21e). */
const { Page, generateMetadata } = legalRoute('imprint');

export { generateMetadata };
export default Page;
