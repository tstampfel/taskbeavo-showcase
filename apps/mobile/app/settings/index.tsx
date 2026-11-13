import { useRouter } from 'expo-router';
import { SettingsScreen } from '../../src/screens/settings-screen';

/** Settings (BEAV-199: Business details; BEAV-190: Legal and licences). */
export default function SettingsRoute() {
  const router = useRouter();
  return (
    <SettingsScreen
      onBack={() => router.back()}
      onBusiness={() => router.push('/settings/business')}
      onLegal={() => router.push('/settings/legal')}
    />
  );
}
