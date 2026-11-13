'use client';

import { useApolloClient } from '@apollo/client/react';
import {
  UPLOAD_CONTENT_TYPES,
  uploadStatusKey,
  type UploadFieldsFragment,
} from '@taskbeavo/graphql';
import { useTranslations } from 'next-intl';
import { useRef, useState, type ChangeEvent } from 'react';
import { Button } from '../components/ui';
import { uploadPickedFile, type PhotoUploadState, type WebUploadPurpose } from './photo-upload';

export interface PhotoUploaderProps {
  /** Task photos only: evidence is taken in the app, and the API refuses it from the web. */
  purpose: WebUploadPurpose;
  /** Called with the READY upload; the screen keeps its `id` to attach it (e.g. postTask). */
  onUploaded?: (upload: UploadFieldsFragment) => void;
  initialState?: PhotoUploadState;
}

/**
 * A "Choose photo" button that uploads the picked photo straight to R2 and shows its
 * progress. Task, booking and evidence screens (BEAV-86, 87, 116, 125) place it.
 */
export function PhotoUploader({
  purpose,
  onUploaded,
  initialState = { phase: 'idle' },
}: PhotoUploaderProps) {
  const t = useTranslations();
  const client = useApolloClient();
  const input = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<PhotoUploadState>(initialState);
  const busy = state.phase === 'uploading' || state.phase === 'processing';
  const statusKey = uploadStatusKey(state);

  async function onPick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    const upload = await uploadPickedFile(client, purpose, file, setState);
    if (upload) onUploaded?.(upload);
  }

  return (
    <div data-testid="photo-uploader" style={{ display: 'grid', gap: 12, justifyItems: 'start' }}>
      <input
        ref={input}
        type="file"
        accept={UPLOAD_CONTENT_TYPES.join(',')}
        hidden
        onChange={(event) => void onPick(event)}
      />
      <Button variant="secondary" disabled={busy} onClick={() => input.current?.click()}>
        {t('uploads.choosePhoto')}
      </Button>
      <p role="status" aria-live="polite" style={{ margin: 0 }}>
        {statusKey ? t(statusKey) : ''}
      </p>
      {state.phase === 'done' && state.upload.url ? (
        // R2's public host isn't a next/image remote pattern yet; screens that list
        // photos add it.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={state.upload.url}
          alt={t('uploads.photoAlt')}
          width={state.upload.width ?? undefined}
          height={state.upload.height ?? undefined}
          style={{ maxWidth: '100%', height: 'auto', borderRadius: 12 }}
        />
      ) : null}
    </div>
  );
}
