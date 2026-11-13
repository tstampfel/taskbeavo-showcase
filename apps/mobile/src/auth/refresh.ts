import { RefreshSessionDocument, type RefreshSessionMutation } from '@taskbeavo/graphql';
import { print } from 'graphql';
import { storedSession, type Refresh } from './session';

/**
 * `refreshSession` over plain fetch, outside Apollo, so the refresh never waits on the
 * auth link that is asking for it. SESSION_EXPIRED gives null (sign in again); any other
 * failure throws, keeping the session for the next try.
 */
export function apiRefresh(httpUrl: string, fetchImpl: typeof fetch = fetch): Refresh {
  return async (refreshToken) => {
    const response = await fetchImpl(httpUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: print(RefreshSessionDocument), variables: { refreshToken } }),
    });
    const body = (await response.json()) as {
      data?: RefreshSessionMutation | null;
      errors?: { extensions?: { code?: string } }[];
    };
    if (body.data?.refreshSession) return storedSession(body.data.refreshSession);
    const code = body.errors?.[0]?.extensions?.code;
    if (code === 'SESSION_EXPIRED') return null;
    throw new Error(`refreshSession failed: ${code ?? response.status}`);
  };
}
