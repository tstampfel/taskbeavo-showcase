import { ApolloLink, HttpLink } from '@apollo/client';
import { GraphQLWsLink } from '@apollo/client/link/subscriptions';
import { ApolloClient, InMemoryCache } from '@apollo/client-integration-nextjs';
import {
  createSubscriptionsClient,
  isSubscription,
  refetchActiveQueries,
  typePolicies,
} from '@taskbeavo/graphql';
import { SAME_ORIGIN_HEADERS } from '../auth/csrf';
import { BROWSER_GRAPHQL_PATH, browserGraphqlWsUrl, serverGraphqlUrl } from './endpoint';
import { telemetryLink } from './telemetry-links';
import { webSocketConnectionParams } from './ws-token';

/**
 * One client per request on the server, one per tab in the browser. Server rendering of
 * client components runs signed out; the signed-in data loads in the browser, where
 * /api/graphql attaches the session from its httpOnly cookie.
 */
export function makeClient() {
  if (typeof window === 'undefined') {
    return new ApolloClient({
      cache: new InMemoryCache({ typePolicies }),
      link: ApolloLink.from([telemetryLink(), new HttpLink({ uri: serverGraphqlUrl() })]),
    });
  }
  // In the browser, subscriptions go over graphql-ws; queries and mutations over HTTP.
  const subscriptions = createSubscriptionsClient({
    wsUrl: browserGraphqlWsUrl(),
    connectionParams: () => webSocketConnectionParams(),
    onReconnected: () => void refetchActiveQueries(client),
  });
  const client = new ApolloClient({
    cache: new InMemoryCache({ typePolicies }),
    link: ApolloLink.from([
      telemetryLink(),
      ApolloLink.split(
        isSubscription,
        new GraphQLWsLink(subscriptions),
        new HttpLink({ uri: BROWSER_GRAPHQL_PATH, headers: SAME_ORIGIN_HEADERS }),
      ),
    ]),
  });
  return client;
}
