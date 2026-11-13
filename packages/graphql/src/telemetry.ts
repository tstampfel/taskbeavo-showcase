/**
 * Telemetry shared by web and mobile: the W3C `traceparent` every GraphQL request carries,
 * how Apollo errors reach Sentry, and the Sentry scrubber.
 *
 * No personal data leaves for Sentry or Honeycomb: names, email, OIB, IBAN, address, phone
 * and chat text (CLAUDE.md, Observability). This mirrors backend config/scrubbing.py:
 *
 * - Request bodies, cookies, query strings and every header except a few technical ones
 *   are dropped. The user keeps only its `id`.
 * - Exception messages and every other string have emails, IBANs, OIBs and phone numbers
 *   replaced; frame variables never leave. Console breadcrumbs lose their text, since
 *   JavaScript has no format string to keep.
 * - In tags, extra and breadcrumb data, a key that names personal data, access details,
 *   free text or a credential has its value replaced, unless it is allow-listed below.
 * - Unknown event keys and contexts are dropped until someone reviews them.
 *
 * Expected GraphQL errors (an error code from the API) are answers, not failures, and never
 * become Sentry events. GraphQL errors without a code are the backend's to report: it
 * continues the request's trace, so its Sentry event carries the same `trace_id`. When one
 * still reaches Sentry here (an unhandled rejection, an error boundary), its message is
 * replaced by its error codes: the server's messages can quote the request's variables.
 *
 * Like @taskbeavo/graphql's Apollo helpers, nothing here imports Sentry: each app passes
 * its own SDK in.
 */

export const FILTERED = '[Filtered]';

/**
 * Tag and attribute keys that may leave without review: the TaskBeavo keys of backend
 * config/telemetry.py's ALLOWED_SPAN_ATTRIBUTES, plus the trace link and the error code.
 * Adding a key needs a review (observability skill), here and in the backend.
 */
export const ALLOWED_TELEMETRY_KEYS: ReadonlySet<string> = new Set([
  'user.id',
  'task.id',
  'offer.id',
  'booking.id',
  'outbox.id',
  'upload.id',
  'upload.purpose',
  'upload.status',
  'booking.status',
  'booking.transition',
  'stripe.event_type',
  'stripe.event_id',
  'stripe.object_id',
  'amount.cents',
  'currency',
  'graphql.operation.name',
  'graphql.operation.type',
  'notification.channel',
  'notification.topic',
  'locale',
  'app.platform',
  'app.version',
  'fiscal.receipt_id',
  'fiscal.status',
  'trace_id',
  'error.code',
]);

// --- traceparent -----------------------------------------------------------------------

export interface TraceContext {
  /** `00-<trace id>-<span id>-01`, the W3C header Django's OTel middleware continues. */
  traceparent: string;
  traceId: string;
  spanId: string;
}

type FillRandom = (bytes: Uint8Array) => void;

/** Web Crypto where it exists; React Native's Hermes has none, and trace IDs needn't be secret. */
const fillRandom: FillRandom = (bytes) => {
  const crypto = (globalThis as { crypto?: { getRandomValues?: FillRandom } }).crypto;
  if (crypto?.getRandomValues) {
    crypto.getRandomValues(bytes);
    return;
  }
  for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
};

function randomHex(byteCount: number, fill: FillRandom): string {
  const bytes = new Uint8Array(byteCount);
  fill(bytes);
  // All zeros is an invalid ID (W3C Trace Context), and the server would start a new trace.
  if (bytes.every((byte) => byte === 0)) bytes[byteCount - 1] = 1;
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** A new sampled trace: each GraphQL operation is the root of its own trace. */
export function newTraceContext(fill: FillRandom = fillRandom): TraceContext {
  const traceId = randomHex(16, fill);
  const spanId = randomHex(8, fill);
  return { traceparent: `00-${traceId}-${spanId}-01`, traceId, spanId };
}

const TRACE_CONTEXT_KEY = 'traceContext';

/**
 * The Apollo context for one operation: a new trace and its `traceparent` header, next to
 * the headers already set. Each app passes it to its own `SetContextLink`.
 */
export function withTraceContext(prevContext: object): {
  headers: Record<string, string>;
  [TRACE_CONTEXT_KEY]: TraceContext;
} {
  const trace = newTraceContext();
  return {
    headers: {
      ...(prevContext as { headers?: Record<string, string> }).headers,
      traceparent: trace.traceparent,
    },
    [TRACE_CONTEXT_KEY]: trace,
  };
}

interface OperationLike {
  operationName?: string;
  operationType?: unknown;
  getContext(): Record<string, unknown>;
}

/** The trace `withTraceContext` started for this operation. */
export function traceContextOf(operation: OperationLike): TraceContext | undefined {
  return operation.getContext()[TRACE_CONTEXT_KEY] as TraceContext | undefined;
}

// --- Apollo errors ---------------------------------------------------------------------

/** The parts of a Sentry SDK the Apollo error link uses. */
export interface ErrorReporter {
  captureException(error: unknown, context: { tags: Record<string, string> }): unknown;
  addBreadcrumb(breadcrumb: {
    category: 'graphql';
    level: 'info';
    message: string;
    data: Record<string, unknown>;
  }): unknown;
}

interface GraphQLErrorLike {
  extensions?: { code?: unknown } | null;
}

function graphQLErrorsOf(error: unknown): GraphQLErrorLike[] | undefined {
  // Apollo Client 4's CombinedGraphQLErrors, checked by name: web and mobile each have
  // their own copy of the class.
  const candidate = error as { name?: unknown; errors?: unknown } | null | undefined;
  if (candidate?.name !== 'CombinedGraphQLErrors' || !Array.isArray(candidate.errors)) {
    return undefined;
  }
  return candidate.errors as GraphQLErrorLike[];
}

function errorCode(error: GraphQLErrorLike): string | undefined {
  const code = error.extensions?.code;
  return typeof code === 'string' ? code : undefined;
}

/** The API's error code (`extensions.code`) of the first GraphQL error, if any. */
export function apiErrorCode(error: unknown): string | undefined {
  const [first] = graphQLErrorsOf(error) ?? [];
  return first === undefined ? undefined : errorCode(first);
}

/** GraphQL errors that all carry an API error code, such as UNAUTHENTICATED. */
export function isExpectedApolloError(error: unknown): boolean {
  const errors = graphQLErrorsOf(error);
  return errors !== undefined && errors.length > 0 && errors.every((e) => errorCode(e));
}

/**
 * Apollo errors whose message joins the server's messages, which can quote variables
 * ("got invalid value {street: …}") or a database row. Only their codes may leave.
 */
const SERVER_MESSAGE_ERRORS = new Set(['CombinedGraphQLErrors', 'CombinedProtocolErrors']);
const SERVER_MESSAGE_PLACEHOLDER = 'GraphQL errors';

function serverMessageReplacement(error: unknown): string | undefined {
  const name = (error as { name?: unknown } | null | undefined)?.name;
  if (typeof name !== 'string' || !SERVER_MESSAGE_ERRORS.has(name)) return undefined;
  const errors = (error as { errors?: unknown }).errors;
  if (!Array.isArray(errors) || errors.length === 0) return SERVER_MESSAGE_PLACEHOLDER;
  const codes = (errors as GraphQLErrorLike[]).map((e) => errorCode(e) ?? 'UNKNOWN');
  return `${SERVER_MESSAGE_PLACEHOLDER}: ${codes.join(', ')}`;
}

// What fetch throws while the device is offline (Chrome, React Native, Safari, Firefox).
const OFFLINE_MESSAGES = new Set([
  'Failed to fetch',
  'Network request failed',
  'Load failed',
  'NetworkError when attempting to fetch resource.',
]);

function isOffline(error: unknown): boolean {
  return error instanceof TypeError && OFFLINE_MESSAGES.has(error.message);
}

/**
 * The Apollo `ErrorLink` handler. GraphQL errors and offline failures become a breadcrumb
 * with the operation name, codes and trace ID; anything else (a 5xx, an unreadable
 * response) is reported, tagged with the operation and its trace. Variables never leave.
 */
export function reportApolloError(
  reporter: ErrorReporter,
  error: unknown,
  operation: OperationLike,
): void {
  const name = operation.operationName ?? 'anonymous';
  const traceId = traceContextOf(operation)?.traceId;
  const errors = graphQLErrorsOf(error);
  if (errors || isOffline(error)) {
    const data: Record<string, unknown> = {};
    if (errors) data.codes = errors.map((e) => errorCode(e) ?? 'UNKNOWN');
    else data.offline = true;
    if (traceId) data.trace_id = traceId;
    reporter.addBreadcrumb({ category: 'graphql', level: 'info', message: name, data });
    return;
  }
  const tags: Record<string, string> = { 'graphql.operation.name': name };
  if (typeof operation.operationType === 'string') {
    tags['graphql.operation.type'] = operation.operationType;
  }
  if (traceId) tags.trace_id = traceId;
  reporter.captureException(error, { tags });
}

// --- Sentry scrubbing ------------------------------------------------------------------

/** The Sentry event fields the scrubber reads; everything else passes through the allow-list. */
export interface SentryEventLike {
  message?: string;
  request?: Record<string, unknown>;
  user?: Record<string, unknown>;
  exception?: { values?: ExceptionLike[] };
  threads?: { values?: ExceptionLike[] };
  contexts?: Record<string, unknown>;
  tags?: Record<string, unknown>;
  extra?: Record<string, unknown>;
  breadcrumbs?: Breadcrumb[];
  logentry?: { message?: string; params?: unknown[] };
  [key: string]: unknown;
}

interface ExceptionLike {
  type?: string;
  value?: string;
  stacktrace?: { frames?: Record<string, unknown>[] };
  [key: string]: unknown;
}

export interface Breadcrumb {
  category?: string;
  message?: string;
  level?: string;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}

// Mirrors backend core/payloads.py's _PII_WORDS and config/scrubbing.py's extra words.
const SENSITIVE_KEY_WORDS = new Set([
  'name',
  'email',
  'phone',
  'contact',
  'dob',
  'birth',
  'oib',
  'iban',
  'card',
  'address',
  'street',
  'city',
  'postcode',
  'zip',
  'lat',
  'lng',
  'latitude',
  'longitude',
  'location',
  'gps',
  'doorbell',
  'floor',
  'gate',
  'access',
  'key',
  'message',
  'chat',
  'text',
  'body',
  'description',
  'note',
  'notes',
  'comment',
  'token',
  'password',
  'secret',
  'mobile',
  'first',
  'last',
  'surname',
  'username',
  'user',
  'ip',
  'addr',
  'cookie',
  'cookies',
  'authorization',
  'auth',
  'session',
  'csrf',
  'credentials',
  'dsn',
  'signature',
  'query',
  'variables',
  'data',
  'content',
  'reply',
  'review',
  'bio',
  'params',
]);

const VALUE_PATTERNS = [
  // Email addresses.
  /[^\s@<>"']+@[^\s@<>"']+\.[A-Za-z]{2,}/g,
  // IBANs, with or without the four-character grouping (HR has 21 characters).
  /\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b/g,
  // OIBs: exactly 11 digits.
  /(?<![\d.])\d{11}(?![\d.])/g,
  // International phone numbers: +385 91 234 5678, 00385912345678.
  /(?<![\w+])(?:\+|00)\d[\d \t/().-]{6,}\d/g,
  // Croatian national numbers: 091 234 5678, 020/123-456.
  /(?<!\w)0\d{1,2}[ /-]?\d{3}[ -]?\d{3,4}(?!\w)/g,
];

const EVENT_KEYS = new Set([
  'event_id',
  'level',
  'platform',
  'timestamp',
  'start_timestamp',
  'logger',
  'server_name',
  'release',
  'environment',
  'dist',
  'sdk',
  'modules',
  'tags',
  'extra',
  'contexts',
  'request',
  'user',
  'exception',
  'threads',
  'breadcrumbs',
  'logentry',
  'message',
  'fingerprint',
  'transaction',
  'transaction_info',
  'type',
  // Source map and native symbol references: what makes stack traces readable.
  'debug_meta',
  'sdkProcessingMetadata',
  '_meta',
]);
// Runtime facts the SDKs fill in. `state`, `response` and anything new are dropped.
const CONTEXT_KEYS = new Set([
  'trace',
  'otel',
  'runtime',
  'os',
  'device',
  'app',
  'browser',
  'culture',
  'react',
  'react_native_context',
  'cloud_resource',
]);
// The user agent stays: it is how Sentry tells browsers apart, and names no one.
const REQUEST_HEADERS = new Set([
  'content-type',
  'content-length',
  'host',
  'user-agent',
  'traceparent',
]);
// Breadcrumb data keys holding URLs, whose query string and fragment are dropped.
const URL_KEYS = ['url', 'from', 'to'];
// The attributes Sentry puts in a clicked element's selector; their values can be labels.
const SELECTOR_ATTRIBUTE_VALUE = /\[(aria-label|title|alt|name|placeholder)=(["']).*?\2\]/g;

function keyWords(key: string): string[] {
  return key
    .split(/[_\W]+|(?<=[a-z])(?=[A-Z])/)
    .filter(Boolean)
    .map((word) => word.toLowerCase());
}

function isSensitiveKey(key: string): boolean {
  return !ALLOWED_TELEMETRY_KEYS.has(key) && keyWords(key).some((w) => SENSITIVE_KEY_WORDS.has(w));
}

export function scrubText(text: string): string {
  return VALUE_PATTERNS.reduce((result, pattern) => result.replace(pattern, FILTERED), text);
}

/** Recursively replace values under sensitive keys and PII patterns in strings. */
export function scrub(value: unknown, options: { keys?: boolean } = {}): unknown {
  const keys = options.keys ?? true;
  if (typeof value === 'string') return scrubText(value);
  if (Array.isArray(value)) return value.map((item) => scrub(item, options));
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        keys && isSensitiveKey(key) ? FILTERED : scrub(item, options),
      ]),
    );
  }
  return value;
}

function stripUrl(url: string): string {
  return url.split(/[?#]/, 1)[0] ?? '';
}

function scrubRequest(request: Record<string, unknown>): Record<string, unknown> {
  const scrubbed: Record<string, unknown> = {};
  if (typeof request.method === 'string') scrubbed.method = request.method;
  if (typeof request.url === 'string') scrubbed.url = stripUrl(request.url);
  const headers = (request.headers ?? {}) as Record<string, unknown>;
  scrubbed.headers = Object.fromEntries(
    Object.entries(headers).filter(([name]) => REQUEST_HEADERS.has(name.toLowerCase())),
  );
  return scrubbed;
}

function scrubException(value: ExceptionLike, serverMessage?: string): ExceptionLike {
  const scrubbed = { ...value };
  if (typeof scrubbed.type === 'string' && SERVER_MESSAGE_ERRORS.has(scrubbed.type)) {
    scrubbed.value = serverMessage ?? SERVER_MESSAGE_PLACEHOLDER;
  } else if (typeof scrubbed.value === 'string') {
    scrubbed.value = scrubText(scrubbed.value);
  }
  if (scrubbed.stacktrace?.frames) {
    scrubbed.stacktrace = {
      ...scrubbed.stacktrace,
      frames: scrubbed.stacktrace.frames.map(({ vars: _vars, ...frame }) => frame),
    };
  }
  return scrubbed;
}

function scrubContexts(contexts: Record<string, unknown>): Record<string, unknown> {
  const scrubbed: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(contexts)) {
    if (!CONTEXT_KEYS.has(key)) continue;
    // These hold SDK runtime facts (the OS `name` is "iOS"), so only strings are checked.
    let context = scrub(value, { keys: false });
    if (key === 'device' && context && typeof context === 'object') {
      // iOS names a phone after its owner ("Ana's iPhone").
      const device = { ...(context as Record<string, unknown>) };
      delete device.name;
      context = device;
    }
    scrubbed[key] = context;
  }
  return scrubbed;
}

export function scrubBreadcrumb(crumb: Breadcrumb): Breadcrumb {
  const scrubbed: Breadcrumb = { ...crumb };
  if (crumb.category === 'console') {
    // Logged arguments can be anything; the level and the fact of the log stay.
    delete scrubbed.data;
    if (crumb.message !== undefined) scrubbed.message = FILTERED;
    return scrubbed;
  }
  if (typeof scrubbed.message === 'string') {
    scrubbed.message = scrubText(scrubbed.message.replace(SELECTOR_ATTRIBUTE_VALUE, '[$1]'));
  }
  if (crumb.data) {
    const data: Record<string, unknown> = { ...crumb.data };
    for (const key of URL_KEYS) {
      if (typeof data[key] === 'string') data[key] = stripUrl(data[key]);
    }
    scrubbed.data = scrub(data) as Record<string, unknown>;
  }
  return scrubbed;
}

/**
 * Scrub a Sentry event (see the module comment). Typed loosely so each SDK's own event type
 * passes through unchanged.
 */
export function scrubEvent<T extends object>(event: T, originalException?: unknown): T {
  const serverMessage = serverMessageReplacement(originalException);
  const scrubbed = Object.fromEntries(
    Object.entries(event).filter(([key]) => EVENT_KEYS.has(key)),
  ) as SentryEventLike;
  if (scrubbed.request) scrubbed.request = scrubRequest(scrubbed.request);
  if ('user' in scrubbed) {
    const id = scrubbed.user?.id;
    scrubbed.user = id !== undefined && id !== null ? { id: String(id as string | number) } : {};
  }
  for (const key of ['exception', 'threads'] as const) {
    const values = scrubbed[key]?.values;
    if (values) {
      scrubbed[key] = {
        ...scrubbed[key],
        values: values.map((value) => scrubException(value, serverMessage)),
      };
    }
  }
  if (typeof scrubbed.message === 'string') {
    scrubbed.message = serverMessage ?? scrubText(scrubbed.message);
  }
  if (scrubbed.logentry) {
    const message = scrubbed.logentry.message;
    scrubbed.logentry = { message: typeof message === 'string' ? scrubText(message) : '' };
  }
  if (scrubbed.contexts) scrubbed.contexts = scrubContexts(scrubbed.contexts);
  if (scrubbed.extra) scrubbed.extra = scrub(scrubbed.extra) as Record<string, unknown>;
  if (scrubbed.tags) scrubbed.tags = scrub(scrubbed.tags) as Record<string, unknown>;
  if (scrubbed.breadcrumbs) scrubbed.breadcrumbs = scrubbed.breadcrumbs.map(scrubBreadcrumb);
  return scrubbed as unknown as T;
}

/** Sentry `beforeSend`: drop expected GraphQL errors, scrub the rest. */
export function sentryBeforeSend<T extends object>(
  event: T,
  hint: { originalException?: unknown } | undefined,
): T | null {
  if (isExpectedApolloError(hint?.originalException)) return null;
  return scrubEvent(event, hint?.originalException);
}

/** Sentry `beforeBreadcrumb`. */
export function sentryBeforeBreadcrumb<T extends object>(crumb: T): T {
  return scrubBreadcrumb(crumb as Breadcrumb) as unknown as T;
}

/** Sentry's EU data region (CLAUDE.md): its DSNs point at *.ingest.de.sentry.io. */
export const SENTRY_EU_HOST_SUFFIX = '.de.sentry.io';
/** The EU region's URL, for sentry-cli source map uploads. */
export const SENTRY_EU_URL = 'https://de.sentry.io/';

/** The DSN if it is in Sentry's EU region; otherwise Sentry stays off. */
export function euSentryDsn(dsn: string | undefined): string | undefined {
  // A regex rather than URL: React Native's URL is incomplete.
  const host = /^https:\/\/[^@/]+@([^/:?#]+)/.exec(dsn ?? '')?.[1]?.toLowerCase();
  return host?.endsWith(SENTRY_EU_HOST_SUFFIX) ? dsn : undefined;
}
