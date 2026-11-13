import { ApolloClient, ApolloLink, HttpLink, InMemoryCache } from '@apollo/client';
import { SetContextLink } from '@apollo/client/link/context';
import { GraphQLWsLink } from '@apollo/client/link/subscriptions';
import {
  createSubscriptionsClient,
  graphqlWsUrl,
  isSubscription,
  refetchActiveQueries,
  typePolicies,
} from '@taskbeavo/graphql';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { AsyncStorageWrapper } from 'apollo3-cache-persist';
import { Platform } from 'react-native';
import { apiRefresh } from '../auth/refresh';
import { createClientStateReset } from '../auth/reset-client-state';
import { authHeaders, createSessionManager } from '../auth/session';
import { secureSessionStore } from '../auth/token-store';
import { forgetLocaleChoice } from '../i18n/locale-store';
import { securePushTokenStore } from '../push/token-store';
import { createCachePersistor, restorePersistedCache } from './cache-persist';
import { graphqlUrl } from './endpoint';
import { telemetryLink } from './telemetry-link';

const httpUrl = graphqlUrl(Platform.OS);

/** The signed-in session, kept in SecureStore. Sign-in screens store what the API returns. */
export const sessionManager = createSessionManager(
  secureSessionStore,
  apiRefresh(httpUrl),
  Date.now,
  // A forced sign-out leaves the phone as a deliberate one does. Deferred: this runs inside
  // the auth link's accessToken() call, which the reset's own requests wait on.
  () => setTimeout(() => void resetAppClientState('signOut'), 0),
);

const authLink = new SetContextLink(async (prevContext) => ({
  headers: authHeaders(
    prevContext.headers as Record<string, string> | undefined,
    await sessionManager.accessToken(),
  ),
}));

const subscriptions = createSubscriptionsClient({
  wsUrl: graphqlWsUrl(httpUrl),
  // Each (re)connect sends a fresh access token in connection_init; signed out it sends
  // none and the server refuses the socket.
  connectionParams: async () => {
    const accessToken = await sessionManager.accessToken();
    return accessToken ? { authToken: accessToken } : {};
  },
  onReconnected: () => void refetchActiveQueries(apolloClient),
});

const cache = new InMemoryCache({ typePolicies });

/** Saves the cache on the phone, so the app opens with the last data while it refetches. */
export const cachePersistor = createCachePersistor(cache, new AsyncStorageWrapper(AsyncStorage));

/** Run once at launch, before the first screen renders. */
export function restoreAppCache(): Promise<void> {
  return restorePersistedCache({
    persistor: cachePersistor,
    versions: AsyncStorage,
    hasSession: async () => (await secureSessionStore.load()) !== null,
  });
}

/** Subscriptions over graphql-ws, queries and mutations over HTTP. */
export const apolloClient: ApolloClient = new ApolloClient({
  cache,
  link: ApolloLink.from([
    telemetryLink(),
    ApolloLink.split(
      isSubscription,
      new GraphQLWsLink(subscriptions),
      ApolloLink.from([authLink, new HttpLink({ uri: httpUrl })]),
    ),
  ]),
});

/**
 * Leaves nothing of the account on this phone (resetClientState). Sign-out and a
 * SESSION_EXPIRED refresh call it; account deletion (BEAV-153) calls it with 'accountDeleted'.
 */
export const resetAppClientState = createClientStateReset(() => ({
  client: apolloClient,
  sessions: sessionManager,
  pushTokens: securePushTokenStore,
  persistor: cachePersistor,
  forgetLanguage: forgetLocaleChoice,
}));
