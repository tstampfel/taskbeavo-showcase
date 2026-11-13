import { useLocalSearchParams, useRouter } from 'expo-router';
import { openRanking } from '../../../../src/auth/app-auth';
import { OffersScreen } from '../../../../src/screens/offers-screen';

/** The poster's offers on their task (mockups 4a, 4c). Accept opens checkout for the offer. */
export default function TaskOffersRoute() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  return (
    <OffersScreen
      taskId={id}
      openRanking={openRanking}
      // A push or link may open the offers with nothing behind them: back then goes home.
      onBack={() => (router.canGoBack() ? router.back() : router.replace('/'))}
      onAccept={(offerId) =>
        router.push({
          pathname: '/tasks/[id]/offers/[offerId]/checkout',
          params: { id, offerId },
        })
      }
    />
  );
}
