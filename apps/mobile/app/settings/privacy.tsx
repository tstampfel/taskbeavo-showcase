import { useRouter } from 'expo-router';
import { PrivacyScreen } from '../../src/screens/privacy-screen';

/** Settings → Privacy and data: analytics and marketing consent (BEAV-83). */
export default function PrivacyRoute() {
  const router = useRouter();
  return <PrivacyScreen onBack={() => router.back()} />;
}
