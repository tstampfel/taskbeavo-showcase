import { test as base, expect, type Page, type Route } from '@playwright/test';

/**
 * A hermetic stand-in for the API the browser talks to: /api/graphql answered by operation
 * name, the subscriptions WebSocket answered with the graphql-ws handshake, and anything sent
 * straight to a local Django (localhost:8000) refused. A spec must pass the same whether or not
 * another worktree's compose stack answers on :8000 (BEAV-193).
 *
 * An operation without an answer is not given `{ data: null }`: it gets a GraphQL error and
 * is recorded, and the `stubGraphql` fixture fails the test with its name at teardown.
 */

export type Variables = Record<string, unknown>;
export type Answer = { data?: unknown; errors?: unknown[] };
export type Answers = Record<string, (variables: Variables) => Answer>;

/** The token the stubbed WebSocketToken query hands the socket's connection_init. */
export const WS_TOKEN = 'e2e-ws-token';

export interface GraphqlStub {
  /** Operations posted to /api/graphql, in order; `variables` is `{}` when none were sent. */
  calls: { name: string; variables: Variables }[];
  /** connection_init payloads the stubbed subscriptions socket received. */
  socketInits: Record<string, unknown>[];
  /** Operation names subscribed to over the stubbed socket. */
  subscriptions: string[];
  /** Requests nothing answers: unstubbed operations and anything sent to a local Django. */
  unexpected: string[];
}

const LOCAL_DJANGO = /^https?:\/\/(localhost|127\.0\.0\.1):8000\//;

/** The operation name, read from the document when the body has none (WebSocketToken has none). */
function operationName(body: { operationName?: string | null; query?: string } | null) {
  if (body?.operationName) return body.operationName;
  return /\b(?:query|mutation|subscription)\s+(\w+)/.exec(body?.query ?? '')?.[1] ?? '<anonymous>';
}

async function installStub(page: Page, answers: Answers): Promise<GraphqlStub> {
  const stub: GraphqlStub = { calls: [], socketInits: [], subscriptions: [], unexpected: [] };
  const all: Answers = {
    WebSocketToken: () => ({ data: { webSocketToken: WS_TOKEN } }),
    ...answers,
  };

  await page.route(LOCAL_DJANGO, async (route: Route) => {
    stub.unexpected.push(`${route.request().method()} ${route.request().url()}`);
    await route.abort();
  });
  await page.route('**/api/graphql', async (route: Route) => {
    const body = route.request().postDataJSON() as {
      operationName?: string | null;
      query?: string;
      variables?: Variables | null;
    } | null;
    const name = operationName(body);
    const variables = body?.variables ?? {};
    stub.calls.push({ name, variables });
    const answer = all[name];
    if (!answer) {
      stub.unexpected.push(`/api/graphql ${name}`);
      await route.fulfill({
        json: { data: null, errors: [{ message: `No e2e stub for ${name}` }] },
      });
      return;
    }
    await route.fulfill({ json: answer(variables) });
  });
  // Any /graphql socket: the dev server's NEXT_PUBLIC_GRAPHQL_WS_URL (playwright.config.ts),
  // or ws://localhost:8000/graphql from a dev server started without it. Never the real one.
  await page.routeWebSocket(
    (url) => url.pathname === '/graphql',
    (ws) => {
      ws.onMessage((raw) => {
        const message = JSON.parse(String(raw)) as {
          id?: string;
          type: string;
          payload?: Record<string, unknown> & { operationName?: string; query?: string };
        };
        if (message.type === 'connection_init') {
          stub.socketInits.push(message.payload ?? {});
          ws.send(JSON.stringify({ type: 'connection_ack' }));
        } else if (message.type === 'subscribe') {
          // Subscribed, but no events: a spec changes state through its query answers.
          stub.subscriptions.push(operationName(message.payload ?? null));
        } else if (message.type === 'ping') {
          ws.send(JSON.stringify({ type: 'pong' }));
        }
      });
    },
  );
  return stub;
}

/**
 * `test` with a `stubGraphql(answers)` fixture. Every test that uses it fails if the page sent
 * a request the stub had no answer for.
 */
export const test = base.extend<{ stubGraphql: (answers: Answers) => Promise<GraphqlStub> }>({
  stubGraphql: async ({ page }, use) => {
    const stubs: GraphqlStub[] = [];
    await use(async (answers) => {
      const stub = await installStub(page, answers);
      stubs.push(stub);
      return stub;
    });
    for (const stub of stubs) {
      expect(stub.unexpected, 'requests the e2e stub has no answer for').toEqual([]);
    }
  },
});

export { expect };
