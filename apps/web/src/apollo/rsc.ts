import { ApolloLink, HttpLink } from '@apollo/client';
import {
  ApolloClient,
  InMemoryCache,
  registerApolloClient,
} from '@apollo/client-integration-nextjs';
import { typePolicies } from '@taskbeavo/graphql';
import { serverGraphqlUrl } from './endpoint';
import { telemetryLink } from './telemetry-links';

/** Apollo for server components (public SEO pages); one client per request. */
export const { getClient, query, PreloadQuery } = registerApolloClient(
  () =>
    new ApolloClient({
      cache: new InMemoryCache({ typePolicies }),
      link: ApolloLink.from([telemetryLink(), new HttpLink({ uri: serverGraphqlUrl() })]),
    }),
);
