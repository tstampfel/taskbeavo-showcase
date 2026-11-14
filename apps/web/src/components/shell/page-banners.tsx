import { ConsentBanner } from '../consent/consent-banner';
import { TermsBanner } from '../terms-update/terms-banner';

/** The limited-mode terms banner and the cookie banner, in the page flow under the header. */
export function PageBanners() {
  return (
    <>
      <TermsBanner />
      <ConsentBanner />
    </>
  );
}
