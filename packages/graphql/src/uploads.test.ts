import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { Observable } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { typePolicies } from './typePolicies';
import { PhotoUploadError, UPLOAD_MAX_BYTES, uploadPhoto, type PhotoFile } from './uploads';

const ID = '7d0f8a64-3c1e-4b8e-9a52-0f3b1d2c4e5f';
const PUT_URL = 'https://acct.eu.r2.cloudflarestorage.com/private/users/1/x/original?sig=1';

function upload(status: string, extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Upload',
    id: ID,
    purpose: 'TASK_PHOTO',
    status,
    contentType: 'image/jpeg',
    sizeBytes: 3,
    width: null,
    height: null,
    rejectionCode: null,
    url: null,
    ...extra,
  };
}

type Reply =
  Record<string, unknown> | { errors: { message: string; extensions: { code: string } }[] };

/** An Apollo client whose server answers each operation from `replies`, in order. */
function clientWith(replies: Record<string, Reply[]>) {
  const calls: { operationName: string; variables: Record<string, unknown> }[] = [];
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        calls.push({
          operationName: operation.operationName ?? '',
          variables: operation.variables,
        });
        const reply = replies[operation.operationName ?? '']?.shift();
        if (!reply) throw new Error(`No reply for ${operation.operationName}`);
        observer.next(
          'errors' in reply ? { errors: reply.errors as { message: string }[] } : { data: reply },
        );
        observer.complete();
      }),
  );
  return { client: new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link }), calls };
}

const target = {
  createUploadUrl: {
    __typename: 'UploadTarget',
    id: ID,
    url: PUT_URL,
    method: 'PUT',
    contentType: 'image/jpeg',
    expiresAt: '2026-10-30T10:00:00+00:00',
    upload: upload('PENDING'),
  },
};

const jpeg: PhotoFile = { body: new Uint8Array([1, 2, 3]), contentType: 'image/jpeg', size: 3 };
const okPut = () => vi.fn(async () => ({ ok: true }));

describe('uploadPhoto', () => {
  it('presigns, PUTs the file to R2 with the signed type, completes and follows it to READY', async () => {
    const { client, calls } = clientWith({
      CreateUploadUrl: [target],
      CompleteUpload: [{ completeUpload: upload('PROCESSING') }],
      Upload: [
        { upload: upload('PROCESSING') },
        { upload: upload('READY', { url: 'https://photos/x.jpg' }) },
      ],
    });
    const put = okPut();
    const phases: string[] = [];

    const done = await uploadPhoto({
      client,
      purpose: 'TASK_PHOTO',
      file: jpeg,
      fetch: put,
      sleep: async () => {},
      onPhase: (phase) => phases.push(phase),
    });

    expect(done.status).toBe('READY');
    expect(done.url).toBe('https://photos/x.jpg');
    expect(put).toHaveBeenCalledWith(PUT_URL, {
      method: 'PUT',
      headers: { 'Content-Type': 'image/jpeg' },
      body: jpeg.body,
    });
    expect(calls.map((c) => c.operationName)).toEqual([
      'CreateUploadUrl',
      'CompleteUpload',
      'Upload',
      'Upload',
    ]);
    expect(calls[0]?.variables).toEqual({
      input: { purpose: 'TASK_PHOTO', contentType: 'image/jpeg', sizeBytes: 3 },
    });
    expect(phases).toEqual(['uploading', 'processing']);
  });

  it('refuses unsupported types and sizes before calling the API', async () => {
    const { client, calls } = clientWith({});
    const heic = { ...jpeg, contentType: 'image/heic' };
    const huge = { ...jpeg, size: UPLOAD_MAX_BYTES + 1 };

    await expect(uploadPhoto({ client, purpose: 'TASK_PHOTO', file: heic })).rejects.toMatchObject({
      code: 'UPLOAD_TYPE_NOT_ALLOWED',
      messageKey: 'errors.UPLOAD_TYPE_NOT_ALLOWED',
    });
    await expect(uploadPhoto({ client, purpose: 'TASK_PHOTO', file: huge })).rejects.toMatchObject({
      code: 'UPLOAD_TOO_LARGE',
    });
    expect(calls).toEqual([]);
  });

  it('turns an API error into its code', async () => {
    const { client } = clientWith({
      CreateUploadUrl: [
        { errors: [{ message: 'x', extensions: { code: 'TOO_MANY_PENDING_UPLOADS' } }] },
      ],
    });

    const failure = uploadPhoto({ client, purpose: 'TASK_PHOTO', file: jpeg, fetch: okPut() });

    await expect(failure).rejects.toBeInstanceOf(PhotoUploadError);
    await expect(failure).rejects.toMatchObject({ code: 'TOO_MANY_PENDING_UPLOADS' });
  });

  it('reports a refused or failed PUT, and never completes the upload', async () => {
    for (const put of [
      vi.fn(async () => ({ ok: false })),
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    ]) {
      const { client, calls } = clientWith({ CreateUploadUrl: [target] });
      await expect(
        uploadPhoto({ client, purpose: 'TASK_PHOTO', file: jpeg, fetch: put }),
      ).rejects.toMatchObject({ code: 'putFailed', messageKey: 'uploads.errors.putFailed' });
      expect(calls.map((c) => c.operationName)).toEqual(['CreateUploadUrl']);
    }
  });

  it('reports a rejected photo with the server rejection code', async () => {
    const { client } = clientWith({
      CreateUploadUrl: [target],
      CompleteUpload: [{ completeUpload: upload('PROCESSING') }],
      Upload: [{ upload: upload('REJECTED', { rejectionCode: 'UPLOAD_INVALID_IMAGE' }) }],
    });

    await expect(
      uploadPhoto({
        client,
        purpose: 'TASK_PHOTO',
        file: jpeg,
        fetch: okPut(),
        sleep: async () => {},
      }),
    ).rejects.toMatchObject({ code: 'UPLOAD_INVALID_IMAGE' });
  });

  it('gives up when processing takes longer than the timeout', async () => {
    const { client } = clientWith({
      CreateUploadUrl: [target],
      CompleteUpload: [{ completeUpload: upload('PROCESSING') }],
      Upload: [{ upload: upload('PROCESSING') }, { upload: upload('PROCESSING') }],
    });

    await expect(
      uploadPhoto({
        client,
        purpose: 'EVIDENCE_PHOTO',
        file: jpeg,
        fetch: okPut(),
        sleep: async () => {},
        pollIntervalMs: 1000,
        timeoutMs: 2000,
      }),
    ).rejects.toMatchObject({ code: 'timedOut', messageKey: 'uploads.errors.timedOut' });
  });
});
