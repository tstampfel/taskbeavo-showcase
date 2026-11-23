import { useLocalSearchParams, useRouter } from 'expo-router';
import { ConversationScreen } from '../../src/screens/conversation-screen';

/** A poster's and a helper's private chat (BEAV-104); new-message pushes and links open it. */
export default function ConversationRoute() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  return (
    <ConversationScreen
      conversationId={id}
      onOpenBooking={(bookingId) =>
        router.push({ pathname: '/bookings/[id]', params: { id: bookingId } })
      }
      // A push or link may open the conversation with nothing behind it: back then goes home.
      onBack={() => (router.canGoBack() ? router.back() : router.replace('/'))}
    />
  );
}
