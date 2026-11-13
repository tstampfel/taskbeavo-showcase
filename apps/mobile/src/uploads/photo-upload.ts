import {
  failureKey,
  trackPhotoUpload,
  type PhotoUploadState,
  type UploadClient,
  type UploadFieldsFragment,
  type UploadPurpose,
} from '@taskbeavo/graphql';

export type { PhotoUploadState };

/** What the image picker returned for one photo. */
export interface PickedPhoto {
  uri: string;
  mimeType?: string | null;
  fileSize?: number | null;
}

/**
 * Upload a picked photo straight to R2 (the shared flow in @taskbeavo/graphql), reporting
 * each state. The file is read from its local URI as a blob. Never throws: failures end in
 * `{ phase: 'error' }`.
 */
export async function uploadPickedPhoto(
  client: UploadClient,
  purpose: UploadPurpose,
  photo: PickedPhoto,
  onChange: (state: PhotoUploadState) => void,
  request: typeof fetch = fetch,
): Promise<UploadFieldsFragment | null> {
  onChange({ phase: 'uploading' });
  let body: Blob;
  try {
    body = await (await request(photo.uri)).blob();
  } catch (error) {
    onChange({ phase: 'error', messageKey: failureKey(error) });
    return null;
  }
  return trackPhotoUpload(
    {
      client,
      purpose,
      file: {
        body,
        // The picker re-encodes to JPEG when it compresses; it reports the type it wrote.
        contentType: photo.mimeType ?? (body.type || 'image/jpeg'),
        size: body.size || photo.fileSize || 0,
      },
      fetch: (url, init) => request(url, init as RequestInit),
    },
    onChange,
  );
}
