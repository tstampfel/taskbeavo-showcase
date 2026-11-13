import { OperationTypeNode } from 'graphql';
import { createClient, type Client, type EventConnectedListener } from 'graphql-ws';
import { newTraceContext } from './telemetry';

/** The server closes a connection_init without a valid token with this code (backend config/ws.py). */
export const WS_FORBIDDEN = 4403;

/** The subscriptions endpoint for a GraphQL HTTP URL: same host and path, ws:// or wss://. */
export function graphqlWsUrl(httpUrl: string): string {
  // String rewrite rather than URL: React Native's URL can't set `protocol`.
  const match = /^http(s?):\/\//i.exec(httpUrl);
  if (!match) throw new Error(`Not an http(s) GraphQL URL: ${httpUrl}`);
  return `ws${match[1]?.toLowerCase() ?? ''}://${httpUrl.slice(match[0].length)}`;
}

/**
 * Retry unless the server refused our credentials: retrying with the same token can't
 * succeed, so the auth code reconnects once it has a fresh one.
 */
export function shouldRetry(errOrCloseEvent: unknown): boolean {
  const code = (errOrCloseEvent as { code?: unknown } | null)?.code;
  return code !== WS_FORBIDDEN;
}

/** A `connected` listener that calls `onReconnected` for every connection after a drop. */
export function reconnectListener(onReconnected: () => void): EventConnectedListener {
  return (_socket, _payload, wasRetry) => {
    if (wasRetry) onReconnected();
  };
}

export interface RealtimeOptions {
  /** ws:// or wss:// URL of the subscriptions endpoint. */
  wsUrl: string;
  /** The connection_init payload. Sign-in (BEAV-70) sends the access token as `authToken`. */
  connectionParams: () => Record<string, unknown> | Promise<Record<string, unknown>>;
  /** The socket came back after a drop. Events sent while it was down are not replayed. */
  onReconnected: () => void;
}

/**
 * The connection_init payload with a new trace's `traceparent`, so a connection is traced
 * like an HTTP request. The backend reads only `authToken` today; continuing this trace in
 * the WebSocket consumer is backend work.
 */
export async function connectionParamsWithTrace(
  connectionParams: RealtimeOptions['connectionParams'],
): Promise<Record<string, unknown>> {
  return { ...(await connectionParams()), traceparent: newTraceContext().traceparent };
}

/** The graphql-ws client. It connects lazily, on the first subscription. */
export function createSubscriptionsClient(options: RealtimeOptions): Client {
  return createClient({
    url: options.wsUrl,
    connectionParams: () => connectionParamsWithTrace(options.connectionParams),
    lazy: true,
    // Keeps idle sockets open through Render's proxy and notices dead ones.
    keepAlive: 20_000,
    retryAttempts: Infinity,
    shouldRetry,
    on: { connected: reconnectListener(options.onReconnected) },
  });
}

// The helpers below take Apollo objects structurally: web and mobile resolve separate
// copies of @apollo/client (different React peers), whose classes don't type-check
// against each other. Each app builds its link with its own ApolloLink:
//   ApolloLink.split(isSubscription, new GraphQLWsLink(createSubscriptionsClient(o)), http)

/** True for subscriptions, which go over the WebSocket; queries and mutations use HTTP. */
export function isSubscription(operation: { operationType: OperationTypeNode }): boolean {
  return operation.operationType === OperationTypeNode.SUBSCRIPTION;
}

/**
 * Re-runs every query a mounted component is watching. Called when the subscription
 * reconnects and when the app returns to the foreground, since missed events aren't
 * replayed.
 */
export function refetchActiveQueries(client: {
  refetchQueries(options: { include: 'active' }): unknown;
}): unknown {
  return client.refetchQueries({ include: 'active' });
}
