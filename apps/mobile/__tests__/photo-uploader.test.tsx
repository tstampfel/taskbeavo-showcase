import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { MockedProvider } from '@apollo/client/testing/react';
import { typePolicies } from '@taskbeavo/graphql';
import { catalogues, locales } from '@taskbeavo/i18n';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import * as ImagePicker from 'expo-image-picker';
import { Linking } from 'react-native';
import { Observable } from 'rxjs';
import { i18n } from '../src/i18n';
import { uploadPickedPhoto, type PhotoUploadState } from '../src/uploads/photo-upload';
import { PhotoUploader } from '../src/uploads/photo-uploader';

jest.mock('expo-image-picker', () => ({
  getCameraPermissionsAsync: jest.fn(),
  requestCameraPermissionsAsync: jest.fn(),
  launchCameraAsync: jest.fn(),
  launchImageLibraryAsync: jest.fn(),
}));

const picker = jest.mocked(ImagePicker);
const ID = '7d0f8a64-3c1e-4b8e-9a52-0f3b1d2c4e5f';
const PUT_URL = 'https://acct.eu.r2.cloudflarestorage.com/private/users/1/x/upload?sig=1';
const PHOTO = { uri: 'file:///var/mobile/photo.jpg', mimeType: 'image/jpeg', fileSize: 3 };
const PICKED = { canceled: false, assets: [PHOTO] } as unknown as ImagePicker.ImagePickerResult;

function upload(status: string, extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Upload',
    id: ID,
    purpose: 'EVIDENCE_PHOTO',
    status,
    contentType: 'image/jpeg',
    sizeBytes: 3,
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

function api() {
  return clientWith({
    CreateUploadUrl: [
      {
        createUploadUrl: {
          __typename: 'UploadTarget',
          id: ID,
          url: PUT_URL,
          method: 'PUT',
          contentType: 'image/jpeg',
          expiresAt: '2026-10-30T10:00:00+00:00',
          upload: upload('PENDING'),
        },
      },
    ],
    CompleteUpload: [{ completeUpload: upload('READY', { url: 'https://r2/display.jpg?sig=2' }) }],
  });
}

/** fetch: the picked file's URI gives a blob, the R2 PUT answers `putOk`. */
function fakeFetch(putOk = true) {
  const blob = { size: 3, type: 'image/jpeg' };
  return jest.fn(async (url: string) =>
    url === PHOTO.uri ? { blob: async () => blob } : { ok: putOk },
  ) as unknown as jest.MockedFunction<typeof fetch>;
}

describe('uploadPickedPhoto', () => {
  it('reads the picked file and PUTs it straight to R2', async () => {
    const request = fakeFetch();
    const states: PhotoUploadState[] = [];

    const done = await uploadPickedPhoto(
      api(),
      'EVIDENCE_PHOTO',
      PHOTO,
      (s) => states.push(s),
      request,
    );

    expect(done?.status).toBe('READY');
    expect(request).toHaveBeenCalledWith(PUT_URL, {
      method: 'PUT',
      headers: { 'Content-Type': 'image/jpeg' },
      body: { size: 3, type: 'image/jpeg' },
    });
    expect(states.map((s) => s.phase)).toEqual(['uploading', 'uploading', 'processing', 'done']);
  });

  it('ends in an error state with the message key when the PUT fails', async () => {
    const states: PhotoUploadState[] = [];

    const done = await uploadPickedPhoto(
      api(),
      'EVIDENCE_PHOTO',
      PHOTO,
      (s) => states.push(s),
      fakeFetch(false),
    );

    expect(done).toBeNull();
    expect(states.at(-1)).toEqual({ phase: 'error', messageKey: 'uploads.errors.putFailed' });
  });
});

describe('PhotoUploader', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    globalThis.fetch = fakeFetch();
  });

  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  const show = (purpose: 'TASK_PHOTO' | 'EVIDENCE_PHOTO', onUploaded = jest.fn()) =>
    render(
      <MockedProvider>
        <PhotoUploader purpose={purpose} onUploaded={onUploaded} />
      </MockedProvider>,
    );

  it.each(locales)('explains camera access in %s before the system prompt', async (locale) => {
    await i18n.changeLanguage(locale);
    const text = catalogues[locale].uploads;
    picker.getCameraPermissionsAsync.mockResolvedValue({
      granted: false,
      canAskAgain: true,
    } as never);
    await show('EVIDENCE_PHOTO');

    await fireEvent.press(screen.getByText(text.takePhoto));

    expect(await screen.findByText(text.permission.cameraTitle)).toBeOnTheScreen();
    expect(screen.getByText(text.permission.cameraBody)).toBeOnTheScreen();
    expect(screen.getByText(text.permission.continue)).toBeOnTheScreen();
    expect(picker.requestCameraPermissionsAsync).not.toHaveBeenCalled();
  });

  it('asks for the camera only after Continue, then takes and uploads the photo', async () => {
    await i18n.changeLanguage('de');
    picker.getCameraPermissionsAsync.mockResolvedValue({
      granted: false,
      canAskAgain: true,
    } as never);
    picker.requestCameraPermissionsAsync.mockResolvedValue({ granted: true } as never);
    picker.launchCameraAsync.mockResolvedValue(PICKED);
    const onUploaded = jest.fn();
    await render(
      <ApolloProvider client={api()}>
        <PhotoUploader purpose="EVIDENCE_PHOTO" onUploaded={onUploaded} />
      </ApolloProvider>,
    );

    await fireEvent.press(screen.getByTestId('take-photo'));
    const allow = await screen.findByTestId('camera-continue');
    await fireEvent.press(allow);

    await waitFor(() =>
      expect(onUploaded).toHaveBeenCalledWith(expect.objectContaining({ id: ID })),
    );
    expect(picker.launchCameraAsync).toHaveBeenCalledWith({ mediaTypes: ['images'], quality: 0.8 });
    expect(screen.getByTestId('upload-status')).toHaveTextContent(catalogues.de.uploads.done);
    expect(screen.getByLabelText(catalogues.de.uploads.photoAlt)).toBeOnTheScreen();
  });

  it('links to Settings when camera access was refused, without asking again', async () => {
    await i18n.changeLanguage('fr');
    const openSettings = jest.spyOn(Linking, 'openSettings').mockResolvedValue();
    picker.getCameraPermissionsAsync.mockResolvedValue({
      granted: false,
      canAskAgain: false,
    } as never);
    await show('EVIDENCE_PHOTO');

    await fireEvent.press(screen.getByTestId('take-photo'));
    const settings = await screen.findByText(catalogues.fr.uploads.permission.openSettings);
    await fireEvent.press(settings);

    expect(screen.getByText(catalogues.fr.uploads.permission.denied)).toBeOnTheScreen();
    expect(openSettings).toHaveBeenCalled();
    expect(picker.requestCameraPermissionsAsync).not.toHaveBeenCalled();
    expect(picker.launchCameraAsync).not.toHaveBeenCalled();
  });

  it('drops the "access is turned off" note once camera access is allowed in Settings', async () => {
    await i18n.changeLanguage('en');
    picker.getCameraPermissionsAsync.mockResolvedValueOnce({
      granted: false,
      canAskAgain: false,
    } as never);
    picker.launchCameraAsync.mockResolvedValue({ canceled: true, assets: null } as never);
    await show('EVIDENCE_PHOTO');
    await fireEvent.press(screen.getByTestId('take-photo'));
    expect(await screen.findByText(catalogues.en.uploads.permission.denied)).toBeOnTheScreen();

    picker.getCameraPermissionsAsync.mockResolvedValueOnce({ granted: true } as never);
    await fireEvent.press(screen.getByTestId('take-photo'));

    await waitFor(() =>
      expect(screen.queryByText(catalogues.en.uploads.permission.denied)).not.toBeOnTheScreen(),
    );
    expect(picker.launchCameraAsync).toHaveBeenCalled();
  });

  it('lets task photos come from the library without a permission prompt', async () => {
    await i18n.changeLanguage('en');
    picker.launchImageLibraryAsync.mockResolvedValue({ canceled: true, assets: null } as never);
    await show('TASK_PHOTO');

    await fireEvent.press(screen.getByText(catalogues.en.uploads.choosePhoto));

    expect(picker.launchImageLibraryAsync).toHaveBeenCalled();
    expect(picker.getCameraPermissionsAsync).not.toHaveBeenCalled();
  });

  it('takes evidence only from the camera', async () => {
    await show('EVIDENCE_PHOTO');

    expect(screen.getByTestId('take-photo')).toBeOnTheScreen();
    expect(screen.queryByTestId('choose-photo')).toBeNull();
  });
});
