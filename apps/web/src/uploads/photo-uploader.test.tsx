import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { typePolicies } from '@taskbeavo/graphql';
import { TASK_TIME_ZONE, catalogues, locales } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import { renderToStaticMarkup } from 'react-dom/server';
import { Observable } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { uploadPickedFile, type PhotoUploadState } from './photo-upload';
import { PhotoUploader } from './photo-uploader';

const ID = '7d0f8a64-3c1e-4b8e-9a52-0f3b1d2c4e5f';
const PUT_URL = 'https://acct.eu.r2.cloudflarestorage.com/private/users/1/x/upload?sig=1';

function upload(status: string, extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Upload',
    id: ID,
    purpose: 'TASK_PHOTO',
    status,
    contentType: 'image/png',
    sizeBytes: 4,
    width: 640,
    height: 480,
    rejectionCode: null,
    url: null,
    ...extra,
  };
}

/** A client whose API answers each operation from `replies`, in order. */
function clientWith(replies: Record<string, Record<string, unknown>[]>) {
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        const data = replies[operation.operationName ?? '']?.shift();
        if (!data) throw new Error(`No reply for ${operation.operationName}`);
        observer.next({ data });
        observer.complete();
      }),
  );
  return new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
}

const happyApi = () =>
  clientWith({
    CreateUploadUrl: [
      {
        createUploadUrl: {
          __typename: 'UploadTarget',
          id: ID,
          url: PUT_URL,
          method: 'PUT',
          contentType: 'image/png',
          expiresAt: '2026-10-30T10:00:00+00:00',
          upload: upload('PENDING'),
        },
      },
    ],
    CompleteUpload: [
      { completeUpload: upload('READY', { url: 'https://photos.taskbeavo.com/photos/x.png' }) },
    ],
  });

const png = new File([new Uint8Array([137, 80, 78, 71])], 'mama-kitchen.png', {
  type: 'image/png',
});

describe('uploadPickedFile', () => {
  it('PUTs the picked file straight to R2 and reports each phase', async () => {
    const put = vi.fn(async () => new Response(null, { status: 200 }));
    const states: PhotoUploadState[] = [];

    const done = await uploadPickedFile(happyApi(), 'TASK_PHOTO', png, (s) => states.push(s), put);

    expect(done?.url).toBe('https://photos.taskbeavo.com/photos/x.png');
    expect(put).toHaveBeenCalledWith(PUT_URL, {
      method: 'PUT',
      headers: { 'Content-Type': 'image/png' },
      body: png,
    });
    expect(states.map((s) => s.phase)).toEqual(['uploading', 'processing', 'done']);
  });

  it('ends in an error state with the message key, never a throw', async () => {
    const heic = new File([new Uint8Array([1])], 'x.heic', { type: 'image/heic' });
    const states: PhotoUploadState[] = [];

    const done = await uploadPickedFile(happyApi(), 'TASK_PHOTO', heic, (s) => states.push(s));

    expect(done).toBeNull();
    expect(states).toEqual([{ phase: 'error', messageKey: 'errors.UPLOAD_TYPE_NOT_ALLOWED' }]);
  });

  it('reports a refused PUT', async () => {
    const put = vi.fn(async () => new Response(null, { status: 403 }));
    const states: PhotoUploadState[] = [];

    await uploadPickedFile(happyApi(), 'TASK_PHOTO', png, (s) => states.push(s), put);

    expect(states.at(-1)).toEqual({ phase: 'error', messageKey: 'uploads.errors.putFailed' });
  });
});

describe('PhotoUploader', () => {
  const render = (locale: (typeof locales)[number], state?: PhotoUploadState) =>
    renderToStaticMarkup(
      <ApolloProvider client={happyApi()}>
        <NextIntlClientProvider
          locale={locale}
          messages={catalogues[locale]}
          timeZone={TASK_TIME_ZONE}
        >
          <PhotoUploader purpose="TASK_PHOTO" initialState={state} />
        </NextIntlClientProvider>
      </ApolloProvider>,
    );

  it.each(locales)('offers a photo picker limited to JPEG, PNG and WebP in %s', (locale) => {
    const markup = render(locale);
    expect(markup).toContain('accept="image/jpeg,image/png,image/webp"');
    expect(markup).toContain(`>${catalogues[locale].uploads.choosePhoto}</button>`);
    expect(markup).toContain('role="status"');
  });

  it.each(locales)('shows progress, the ready photo and errors in %s', (locale) => {
    const text = catalogues[locale];
    expect(render(locale, { phase: 'processing' })).toContain(text.uploads.processing);
    const done = render(locale, {
      phase: 'done',
      upload: upload('READY', { url: 'https://photos.taskbeavo.com/photos/x.png' }) as never,
    });
    expect(done).toContain(text.uploads.done);
    expect(done).toContain(`alt="${text.uploads.photoAlt}"`);
    expect(render(locale, { phase: 'error', messageKey: 'errors.UPLOAD_TOO_LARGE' })).toContain(
      text.errors.UPLOAD_TOO_LARGE,
    );
  });

  it('disables the button while a photo is uploading', () => {
    expect(render('en', { phase: 'uploading' })).toMatch(/<button[^>]*disabled=""/);
  });
});
