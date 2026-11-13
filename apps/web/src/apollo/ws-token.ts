import { WebSocketTokenDocument } from '@taskbeavo/graphql';
import { print } from 'graphql';
import { SAME_ORIGIN_HEADERS } from '../auth/csrf';
import { BROWSER_GRAPHQL_PATH } from './endpoint';

/**
 * The graphql-ws `connection_init` payload. The access token sits in an httpOnly cookie
 * page code can't read, so each (re)connect asks /api/graphql for a short-lived WebSocket
 * token. Signed out or unreachable, it sends no token and the server refuses the socket.
 */
export async function webSocketConnectionParams(
  fetchImpl: typeof fetch = fetch,
): Promise<Record<string, unknown>> {
  try {
    const response = await fetchImpl(BROWSER_GRAPHQL_PATH, {
      method: 'POST',
      headers: SAME_ORIGIN_HEADERS,
      body: JSON.stringify({ query: print(WebSocketTokenDocument) }),
    });
    const body = (await response.json()) as { data?: { webSocketToken?: unknown } | null };
    const token = body.data?.webSocketToken;
    return typeof token === 'string' ? { authToken: token } : {};
  } catch {
    return {};
  }
}
