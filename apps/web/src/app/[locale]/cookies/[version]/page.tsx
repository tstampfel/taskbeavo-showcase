import { legalRoute } from '../../../../components/legal/legal-route';

/** /{locale}/cookies/{version}: an earlier or scheduled version. */
const { Page, generateMetadata } = legalRoute('cookies');

export { generateMetadata };
export default Page;
