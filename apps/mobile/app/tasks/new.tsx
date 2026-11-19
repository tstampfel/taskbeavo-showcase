import { useRouter } from 'expo-router';
import { PostTaskScreen } from '../../src/screens/post-task-screen';

/** Post a task (mockup 03, BEAV-86). After posting, the new task's detail screen replaces it. */
export default function NewTask() {
  const router = useRouter();
  return (
    <PostTaskScreen
      onClose={() => (router.canGoBack() ? router.back() : router.replace('/'))}
      onPosted={(id) => router.replace({ pathname: '/tasks/[id]', params: { id } })}
      onVerifyPhone={() => router.push('/verify-phone')}
      onAcceptTerms={() => router.push('/terms-update')}
    />
  );
}
