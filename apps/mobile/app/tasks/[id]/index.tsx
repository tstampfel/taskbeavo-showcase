import { useLocalSearchParams, useRouter } from 'expo-router';
import { TaskDetailScreen } from '../../../src/screens/task-detail-screen';

/** Task detail (mockup 05, BEAV-90); task links open it (@taskbeavo/app-links). */
export default function TaskDetail() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  return (
    <TaskDetailScreen
      taskId={id}
      // A push or link may open the task with nothing behind it: back then goes home.
      onBack={() => (router.canGoBack() ? router.back() : router.replace('/'))}
      onOpenOffers={() => router.push({ pathname: '/tasks/[id]/offers', params: { id } })}
    />
  );
}
