import {
  trackPhotoUpload,
  type PhotoUploadState,
  type UploadClient,
  type UploadFieldsFragment,
} from '@taskbeavo/graphql';

export type { PhotoUploadState };

/** The web's photos are task photos: evidence is taken in the app (EVIDENCE_NEEDS_APP). */
export type WebUploadPurpose = 'TASK_PHOTO';

/**
 * Upload a picked `File` straight to R2 (the shared flow in @taskbeavo/graphql), reporting
 * each state. Never throws: failures end in `{ phase: 'error' }`.
 */
export function uploadPickedFile(
  client: UploadClient,
  purpose: WebUploadPurpose,
  file: File,
  onChange: (state: PhotoUploadState) => void,
  put: typeof fetch = fetch,
): Promise<UploadFieldsFragment | null> {
  return trackPhotoUpload(
    {
      client,
      purpose,
      file: { body: file, contentType: file.type, size: file.size },
      fetch: (url, init) => put(url, init as RequestInit),
    },
    onChange,
  );
}
