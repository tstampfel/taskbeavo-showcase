import { describe, expect, it, vi } from 'vitest';
import { webSocketConnectionParams } from './ws-token';

describe('webSocketConnectionParams', () => {
  it('asks /api/graphql for a WebSocket token and sends it as authToken', async () => {
    const fetchMock = vi.fn(async () => Response.json({ data: { webSocketToken: 'ws-jwt' } }));

    expect(await webSocketConnectionParams(fetchMock)).toEqual({ authToken: 'ws-jwt' });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/graphql');
    expect(new Headers(init.headers).get('x-taskbeavo-csrf')).toBe('1');
    expect(String(init.body)).toContain('webSocketToken');
  });

  it('sends no token when signed out', async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({ data: null, errors: [{ extensions: { code: 'UNAUTHENTICATED' } }] }),
    );
    expect(await webSocketConnectionParams(fetchMock)).toEqual({});
  });

  it('sends no token when the request fails', async () => {
    const fetchMock = vi.fn(async () => Promise.reject(new TypeError('offline')));
    expect(await webSocketConnectionParams(fetchMock)).toEqual({});
  });
});
