import { ApolloLink } from '@apollo/client';
import { SetContextLink } from '@apollo/client/link/context';
import { ErrorLink } from '@apollo/client/link/error';
import * as Sentry from '@sentry/react-native';
import { reportApolloError, withTraceContext } from '@taskbeavo/graphql';

/**
 * Starts a trace per operation and sends its `traceparent`, so the app action and its
 * backend spans share one trace in Honeycomb. Failures reach Sentry tagged with the
 * operation name and trace ID; expected GraphQL errors only leave a breadcrumb.
 */
export function telemetryLink(): ApolloLink {
  return ApolloLink.from([
    new SetContextLink(withTraceContext),
    new ErrorLink(({ error, operation }) => reportApolloError(Sentry, error, operation)),
  ]);
}
