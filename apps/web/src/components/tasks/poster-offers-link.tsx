'use client';

import { useQuery } from '@apollo/client/react';
import { TaskListingDocument, ViewerDocument } from '@taskbeavo/graphql';
import { Link } from '../../i18n/navigation';
import styles from './tasks.module.css';

/**
 * The poster's way from their task's public page to its offers (offer emails link here). The
 * page itself is server-rendered without the session, so this island asks again with it, and
 * only once someone is signed in: `isMine` is true for the poster alone.
 */
export function PosterOffersLink({ taskId, label }: { taskId: string; label: string }) {
  const viewer = useQuery(ViewerDocument).data?.viewer;
  const listing = useQuery(TaskListingDocument, {
    variables: { id: taskId },
    skip: !viewer,
    fetchPolicy: 'network-only',
  }).data?.taskListing;
  if (!viewer || !listing?.isMine) return null;
  return (
    <Link href={`/tasks/${taskId}/offers`} className={styles.primaryLink} data-testid="see-offers">
      {label}
    </Link>
  );
}
