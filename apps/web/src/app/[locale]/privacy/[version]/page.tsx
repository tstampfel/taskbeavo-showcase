import { legalRoute } from '../../../../components/legal/legal-route';

/** /{locale}/privacy/{version}: an earlier or scheduled version. */
const { Page, generateMetadata } = legalRoute('privacy');

export { generateMetadata };
export default Page;
