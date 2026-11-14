import { useApolloClient } from '@apollo/client/react';
import { uploadStatusKey, type UploadFieldsFragment, type UploadPurpose } from '@taskbeavo/graphql';
import { colors, radii, spacing } from '@taskbeavo/ui-tokens';
import * as ImagePicker from 'expo-image-picker';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Image, Linking, StyleSheet, View } from 'react-native';
import { Text } from '../components/ui/text';
import { Button, Card, Heading } from '../components/ui';
import { fonts } from '../fonts';
import { uploadPickedPhoto, type PhotoUploadState } from './photo-upload';

export interface PhotoUploaderProps {
  purpose: UploadPurpose;
  /** Called with the READY upload; the screen keeps its `id` to attach it (e.g. postTask). */
  onUploaded?: (upload: UploadFieldsFragment) => void;
}

// The picker compresses, which also re-encodes HEIC camera photos as JPEG.
const PICKER_OPTIONS: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], quality: 0.8 };

type CameraAccess = 'unknown' | 'explain' | 'denied';

/**
 * "Take photo" (and, for task photos, "Choose photo") that uploads straight to R2 and shows
 * progress. Evidence must come from the camera, so it has no library button. Before the
 * system camera prompt, an explanation in the user's language says why it's needed; a
 * refusal links to Settings and never re-prompts. Task, booking and evidence screens
 * (BEAV-86, 87, 116, 125) place it.
 */
export function PhotoUploader({ purpose, onUploaded }: PhotoUploaderProps) {
  const { t } = useTranslation();
  const client = useApolloClient();
  const [state, setState] = useState<PhotoUploadState>({ phase: 'idle' });
  const [cameraAccess, setCameraAccess] = useState<CameraAccess>('unknown');
  const busy = state.phase === 'uploading' || state.phase === 'processing';
  const statusKey = uploadStatusKey(state);

  async function upload(result: ImagePicker.ImagePickerResult) {
    const photo = result.canceled ? undefined : result.assets[0];
    if (!photo) return;
    const done = await uploadPickedPhoto(client, purpose, photo, setState);
    if (done) onUploaded?.(done);
  }

  async function takePhoto() {
    const permission = await ImagePicker.getCameraPermissionsAsync();
    if (!permission.granted) {
      setCameraAccess(permission.canAskAgain ? 'explain' : 'denied');
      return;
    }
    // Allowed since (e.g. in Settings): drop the "access is turned off" note.
    setCameraAccess('unknown');
    await upload(await ImagePicker.launchCameraAsync(PICKER_OPTIONS));
  }

  async function allowCamera() {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    setCameraAccess(permission.granted ? 'unknown' : 'denied');
    if (permission.granted) await upload(await ImagePicker.launchCameraAsync(PICKER_OPTIONS));
  }

  async function choosePhoto() {
    // The system photo picker needs no permission.
    await upload(await ImagePicker.launchImageLibraryAsync(PICKER_OPTIONS));
  }

  if (cameraAccess === 'explain') {
    return (
      <Card testID="camera-explanation">
        <Heading style={styles.title}>{t('uploads.permission.cameraTitle')}</Heading>
        <Text style={styles.body}>{t('uploads.permission.cameraBody')}</Text>
        <Button
          testID="camera-continue"
          label={t('uploads.permission.continue')}
          onPress={() => void allowCamera()}
        />
      </Card>
    );
  }

  return (
    <View style={styles.container} testID="photo-uploader">
      <Button
        testID="take-photo"
        label={t('uploads.takePhoto')}
        busy={busy}
        onPress={() => void takePhoto()}
      />
      {purpose === 'TASK_PHOTO' ? (
        <Button
          testID="choose-photo"
          variant="secondary"
          label={t('uploads.choosePhoto')}
          busy={busy}
          onPress={() => void choosePhoto()}
        />
      ) : null}
      {cameraAccess === 'denied' ? (
        <View style={styles.container} testID="camera-denied">
          <Text style={styles.body}>{t('uploads.permission.denied')}</Text>
          <Button
            variant="ghost"
            label={t('uploads.permission.openSettings')}
            onPress={() => void Linking.openSettings()}
          />
        </View>
      ) : null}
      <Text accessibilityLiveRegion="polite" testID="upload-status" style={styles.body}>
        {statusKey ? t(statusKey) : ''}
      </Text>
      {state.phase === 'done' && state.upload.url ? (
        <Image
          accessibilityLabel={t('uploads.photoAlt')}
          source={{ uri: state.upload.url }}
          style={[
            styles.preview,
            { aspectRatio: (state.upload.width ?? 4) / (state.upload.height ?? 3) },
          ]}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: spacing.md },
  title: { fontFamily: fonts.bold, fontSize: 18, color: colors.ink },
  body: { fontFamily: fonts.regular, fontSize: 16, lineHeight: 22, color: colors.ink },
  preview: { width: '100%', borderRadius: radii.md },
});
