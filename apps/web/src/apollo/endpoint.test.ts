import { describe, expect, it } from 'vitest';
import { BROWSER_GRAPHQL_PATH, browserGraphqlWsUrl, serverGraphqlUrl } from './endpoint';

describe('serverGraphqlUrl', () => {
  it('reads GRAPHQL_URL', () => {
    expect(serverGraphqlUrl({ GRAPHQL_URL: 'https://api.taskbeavo.hr/graphql' })).toBe(
      'https://api.taskbeavo.hr/graphql',
    );
  });

  it('defaults to the local Django server', () => {
    expect(serverGraphqlUrl({})).toBe('http://localhost:8000/graphql');
  });
});

describe('BROWSER_GRAPHQL_PATH', () => {
  it('is same-origin, so the browser needs no CORS and later sends the session cookie', () => {
    expect(BROWSER_GRAPHQL_PATH.startsWith('/')).toBe(true);
  });
});

describe('browserGraphqlWsUrl', () => {
  it('reads NEXT_PUBLIC_GRAPHQL_WS_URL', () => {
    expect(
      browserGraphqlWsUrl({ NEXT_PUBLIC_GRAPHQL_WS_URL: 'wss://api.taskbeavo.hr/graphql' }),
    ).toBe('wss://api.taskbeavo.hr/graphql');
  });

  it('defaults to the local Django server', () => {
    expect(browserGraphqlWsUrl({})).toBe('ws://localhost:8000/graphql');
  });
});
