import type { ApolloClient } from '@apollo/client';
import { refetchActiveQueries } from '@taskbeavo/graphql';
import { useEffect } from 'react';
import { AppState } from 'react-native';

/**
 * Refetches the active queries when the app comes back from the background: the
 * subscription socket doesn't deliver while backgrounded and missed events aren't replayed.
 * Brief `inactive` blips (Control Center, the app switcher) don't count.
 */
export function useRefetchOnForeground(client: ApolloClient): void {
  useEffect(() => {
    let wasInBackground = AppState.currentState === 'background';
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'background') {
        wasInBackground = true;
      } else if (state === 'active' && wasInBackground) {
        wasInBackground = false;
        void refetchActiveQueries(client);
      }
    });
    return () => subscription.remove();
  }, [client]);
}
