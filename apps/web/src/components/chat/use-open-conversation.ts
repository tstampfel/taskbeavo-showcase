import { useMutation } from '@apollo/client/react';
import { OpenConversationDocument } from '@taskbeavo/graphql';
import { useState } from 'react';

/**
 * Opens the poster's and helper's conversation about an offer (the same one each time, also
 * for an accepted offer) and hands its ID to `onOpened`. `failedFor` is the offer whose
 * conversation couldn't be opened, for an inline message next to its button.
 */
export function useOpenConversation(onOpened: (conversationId: string) => void) {
  const [mutate, state] = useMutation(OpenConversationDocument);
  const [pendingFor, setPendingFor] = useState<string | null>(null);
  const [failedFor, setFailedFor] = useState<string | null>(null);
  const open = async (offerId: string) => {
    setFailedFor(null);
    setPendingFor(offerId);
    try {
      const result = await mutate({ variables: { offerId } });
      const id = result.data?.openConversation.id;
      if (id) onOpened(id);
      else setFailedFor(offerId);
    } catch {
      setFailedFor(offerId);
    } finally {
      setPendingFor(null);
    }
  };
  return { open, pendingFor: state.loading ? pendingFor : null, failedFor };
}
