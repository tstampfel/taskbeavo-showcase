import {
  CompleteUploadDocument,
  CreateUploadUrlDocument,
  UploadDocument,
  type CompleteUploadMutation,
  type CreateUploadUrlMutation,
  type UploadFieldsFragment,
  type UploadPurpose,
  type UploadQuery,
} from './generated/graphql';
import { apiErrorCode } from './telemetry';

/**
 * Photo uploads straight to Cloudflare R2, shared by web and mobile (BEAV-63):
 * `createUploadUrl` presigns a PUT, the file goes to R2 without passing the API,
 * `completeUpload` hands it to the server, which strips its metadata, and the upload is
 * followed until READY. The server checks everything again; the checks here only save
 * a round trip.
 */

/** What the API accepts (backend/uploads/images.py). */
export const UPLOAD_CONTENT_TYPES: readonly string[] = ['image/jpeg', 'image/png', 'image/webp'];
export const UPLOAD_MAX_BYTES = 10 * 1024 * 1024;

/**
 * Why an upload failed: an API error code (translated as `errors.<CODE>`), or one of the
 * client-side codes below (translated as `uploads.errors.<code>`).
 */
export type UploadFailure = string;
export const UPLOAD_PUT_FAILED = 'putFailed';
export const UPLOAD_TIMED_OUT = 'timedOut';
export const UPLOAD_FAILED = 'failed';
const CLIENT_MESSAGE_KEYS: Record<string, string> = {
  [UPLOAD_PUT_FAILED]: 'uploads.errors.putFailed',
  [UPLOAD_TIMED_OUT]: 'uploads.errors.timedOut',
  [UPLOAD_FAILED]: 'uploads.errors.failed',
};

export class PhotoUploadError extends Error {
  readonly code: UploadFailure;

  constructor(code: UploadFailure) {
    super(`Photo upload failed: ${code}`);
    this.name = 'PhotoUploadError';
    this.code = code;
  }

  /** The i18n key of the message to show. */
  get messageKey(): string {
    return CLIENT_MESSAGE_KEYS[this.code] ?? `errors.${this.code}`;
  }
}

export interface PhotoFile {
  /** A `Blob`/`File` on the web; on mobile, the blob fetched from the picked file's URI. */
  body: unknown;
  contentType: string;
  size: number;
}

/** The part of `fetch` the PUT needs; this package is built without the DOM types. */
export type PutFile = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: unknown },
) => Promise<{ ok: boolean }>;

interface Timers {
  fetch?: PutFile;
  setTimeout: (callback: () => void, ms: number) => unknown;
}
const host = globalThis as unknown as Timers;

export type UploadPhase = 'uploading' | 'processing';

/**
 * The part of an `ApolloClient` the flow uses. Structural, because web and mobile each
 * resolve their own copy of @apollo/client.
 */
export interface UploadClient {
  mutate(options: { mutation: unknown; variables?: unknown }): Promise<unknown>;
  query(options: { query: unknown; variables?: unknown; fetchPolicy?: unknown }): Promise<unknown>;
}

export interface UploadPhotoOptions {
  client: UploadClient;
  purpose: UploadPurpose;
  file: PhotoFile;
  onPhase?: (phase: UploadPhase) => void;
  /** Injected in tests; defaults to the global `fetch`. */
  fetch?: PutFile;
  sleep?: (ms: number) => Promise<void>;
  pollIntervalMs?: number;
  timeoutMs?: number;
}

/** Upload one photo; resolves with the READY upload, or throws a `PhotoUploadError`. */
export async function uploadPhoto({
  client,
  purpose,
  file,
  onPhase,
  fetch: put = (url, init) => host.fetch!(url, init),
  sleep = (ms) => new Promise((resolve) => host.setTimeout(resolve, ms)),
  pollIntervalMs = 1000,
  timeoutMs = 60_000,
}: UploadPhotoOptions): Promise<UploadFieldsFragment> {
  const contentType = file.contentType.toLowerCase();
  if (!UPLOAD_CONTENT_TYPES.includes(contentType)) {
    throw new PhotoUploadError('UPLOAD_TYPE_NOT_ALLOWED');
  }
  if (file.size <= 0 || file.size > UPLOAD_MAX_BYTES) {
    throw new PhotoUploadError('UPLOAD_TOO_LARGE');
  }

  onPhase?.('uploading');
  const created = await api(() =>
    client.mutate({
      mutation: CreateUploadUrlDocument,
      variables: { input: { purpose, contentType, sizeBytes: file.size } },
    }),
  );
  const target = dataOf<CreateUploadUrlMutation>(created)?.createUploadUrl;
  if (!target) throw new PhotoUploadError(UPLOAD_FAILED);

  let response: { ok: boolean };
  try {
    // Exactly the signed Content-Type; the length comes from the body and is signed too.
    response = await put(target.url, {
      method: target.method,
      headers: { 'Content-Type': target.contentType },
      body: file.body,
    });
  } catch {
    throw new PhotoUploadError(UPLOAD_PUT_FAILED);
  }
  if (!response.ok) throw new PhotoUploadError(UPLOAD_PUT_FAILED);

  onPhase?.('processing');
  const completed = await api(() =>
    client.mutate({ mutation: CompleteUploadDocument, variables: { id: target.id } }),
  );
  let upload: UploadFieldsFragment | null | undefined =
    dataOf<CompleteUploadMutation>(completed)?.completeUpload;
  let polls = Math.ceil(timeoutMs / pollIntervalMs);
  while (upload?.status === 'PROCESSING' || upload?.status === 'PENDING') {
    if (polls-- <= 0) throw new PhotoUploadError(UPLOAD_TIMED_OUT);
    await sleep(pollIntervalMs);
    const found = await api(() =>
      client.query({
        query: UploadDocument,
        variables: { id: target.id },
        fetchPolicy: 'network-only',
      }),
    );
    upload = dataOf<UploadQuery>(found)?.upload;
  }
  if (upload?.status === 'READY') return upload;
  throw new PhotoUploadError(upload?.rejectionCode ?? UPLOAD_FAILED);
}

function dataOf<T>(result: unknown): T | undefined {
  return (result as { data?: T } | undefined)?.data;
}

async function api<T>(call: () => Promise<T>): Promise<T> {
  try {
    return await call();
  } catch (error) {
    throw new PhotoUploadError(apiErrorCode(error) ?? UPLOAD_FAILED);
  }
}

/** Where one photo upload is, for an uploader to show. */
export type PhotoUploadState =
  | { phase: 'idle' }
  | { phase: 'uploading' | 'processing' }
  | { phase: 'done'; upload: UploadFieldsFragment }
  | { phase: 'error'; messageKey: string };

/** The i18n key of an uploader's status line, or null while idle. */
export function uploadStatusKey(state: PhotoUploadState): string | null {
  switch (state.phase) {
    case 'uploading':
      return 'uploads.uploading';
    case 'processing':
      return 'uploads.processing';
    case 'done':
      return 'uploads.done';
    case 'error':
      return state.messageKey;
    default:
      return null;
  }
}

/**
 * `uploadPhoto` for an uploader: reports every state to `onChange` and never throws.
 * Resolves with the READY upload, or null after reporting `{ phase: 'error' }`.
 */
export async function trackPhotoUpload(
  options: Omit<UploadPhotoOptions, 'onPhase'>,
  onChange: (state: PhotoUploadState) => void,
): Promise<UploadFieldsFragment | null> {
  try {
    const upload = await uploadPhoto({ ...options, onPhase: (phase) => onChange({ phase }) });
    onChange({ phase: 'done', upload });
    return upload;
  } catch (error) {
    onChange({ phase: 'error', messageKey: failureKey(error) });
    return null;
  }
}

/** The i18n key of the message for any error an upload ends with. */
export function failureKey(error: unknown): string {
  return error instanceof PhotoUploadError ? error.messageKey : CLIENT_MESSAGE_KEYS[UPLOAD_FAILED]!;
}
