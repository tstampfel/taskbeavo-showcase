import { legalRoute } from '../../../../components/legal/legal-route';

/** /{locale}/imprint/{version}: an earlier or scheduled version. */
const { Page, generateMetadata } = legalRoute('imprint');

export { generateMetadata };
export default Page;
