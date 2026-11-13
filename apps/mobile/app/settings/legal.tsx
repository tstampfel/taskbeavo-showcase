import { useRouter } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { openLegal } from '../../src/auth/app-auth';
import { LegalLicencesScreen } from '../../src/screens/legal-licences-screen';

/** Settings → Legal and licences: the legal pages and MaxMind's GeoLite attribution (BEAV-190). */
export default function LegalLicencesRoute() {
  const router = useRouter();
  return (
    <LegalLicencesScreen
      onBack={() => router.back()}
      openLegal={openLegal}
      openUrl={(url) => void WebBrowser.openBrowserAsync(url)}
    />
  );
}
