import { ApolloClient, InMemoryCache, type ApolloLink } from '@apollo/client';
import { OperationTypeNode } from 'graphql';
import { describe, expect, it, vi } from 'vitest';
import {
  WS_FORBIDDEN,
  connectionParamsWithTrace,
  graphqlWsUrl,
  isSubscription,
  reconnectListener,
  refetchActiveQueries,
  shouldRetry,
} from './realtime';

describe('graphqlWsUrl', () => {
  it('keeps host and path and switches to the WebSocket scheme', () => {
    expect(graphqlWsUrl('https://api.taskbeavo.hr/graphql')).toBe('wss://api.taskbeavo.hr/graphql');
    expect(graphqlWsUrl('http://10.0.2.2:8000/graphql')).toBe('ws://10.0.2.2:8000/graphql');
  });

  it('rejects anything but http(s)', () => {
    expect(() => graphqlWsUrl('ftp://api.taskbeavo.hr/graphql')).toThrow('Not an http(s)');
  });
});

describe('connectionParamsWithTrace', () => {
  it('adds a traceparent to the connection_init payload and keeps the token', async () => {
    const params = await connectionParamsWithTrace(async () => ({ authToken: 't' }));
    expect(params.authToken).toBe('t');
    expect(params.traceparent).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/);
  });
});

describe('shouldRetry', () => {
  it('stops when the server refuses the token', () => {
    expect(shouldRetry({ code: WS_FORBIDDEN })).toBe(false);
  });

  it('retries dropped connections and network errors', () => {
    expect(shouldRetry({ code: 1006 })).toBe(true);
    expect(shouldRetry(new Error('network down'))).toBe(true);
    expect(shouldRetry(null)).toBe(true);
  });
});

describe('reconnectListener', () => {
  it('fires on reconnects, not on the first connection', () => {
    const onReconnected = vi.fn();
    const listener = reconnectListener(onReconnected);

    listener({}, undefined, false);
    expect(onReconnected).not.toHaveBeenCalled();

    listener({}, undefined, true);
    expect(onReconnected).toHaveBeenCalledTimes(1);
  });
});

describe('isSubscription', () => {
  it('sends only subscriptions over the WebSocket', () => {
    expect(isSubscription({ operationType: OperationTypeNode.SUBSCRIPTION })).toBe(true);
    expect(isSubscription({ operationType: OperationTypeNode.QUERY })).toBe(false);
    expect(isSubscription({ operationType: OperationTypeNode.MUTATION })).toBe(false);
  });
});

describe('refetchActiveQueries', () => {
  it('refetches the active queries', async () => {
    const client = new ApolloClient({ cache: new InMemoryCache(), link: {} as ApolloLink });
    const refetch = vi.spyOn(client, 'refetchQueries').mockResolvedValue([] as never);

    await refetchActiveQueries(client);

    expect(refetch).toHaveBeenCalledWith({ include: 'active' });
  });
});
