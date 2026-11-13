import { legalRoute } from '../../../../components/legal/legal-route';

/** /{locale}/terms/{version}: an earlier or scheduled version. */
const { Page, generateMetadata } = legalRoute('terms');

export { generateMetadata };
export default Page;
