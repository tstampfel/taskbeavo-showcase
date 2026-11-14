import {
  borderWidth,
  colors,
  minTapTarget,
  radii,
  shadows,
  spacing,
  surfaces,
} from '@taskbeavo/ui-tokens';
import type { ReactNode } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Heading } from './heading';
import { textStyle } from './text-style';

/** The width beside the sheet title: the panel's padding, the close button and the gap. */
const SHEET_TITLE_INSET = 2 * spacing.lg + minTapTarget + spacing.sm;

export interface SheetProps {
  visible: boolean;
  onClose: () => void;
  /** Heading, already translated. */
  title: string;
  /** Accessibility label of the close button and the backdrop, already translated. */
  closeLabel: string;
  children: ReactNode;
  testID?: string;
}

/**
 * Bottom sheet from the mockups' `.sheet`. The content scrolls, so it still fits at the largest
 * text size; Android back and the iOS escape gesture call `onClose`.
 */
export function Sheet({ visible, onClose, title, closeLabel, children, testID }: SheetProps) {
  const insets = useSafeAreaInsets();
  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
      statusBarTranslucent
    >
      <View style={styles.root}>
        {/* Labelled for TalkBack, which ignores accessibilityViewIsModal; VoiceOver skips it. */}
        <Pressable
          style={styles.scrim}
          onPress={onClose}
          accessibilityRole="button"
          accessibilityLabel={closeLabel}
          testID={testID ? `${testID}-scrim` : undefined}
        />
        <View
          accessibilityViewIsModal
          testID={testID}
          style={[
            styles.panel,
            { paddingBottom: Math.max(insets.bottom, spacing.lg) + spacing.lg },
          ]}
        >
          <View
            style={styles.grab}
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
          />
          <View style={styles.header}>
            <Heading style={[textStyle('h2'), styles.title]} inset={SHEET_TITLE_INSET}>
              {title}
            </Heading>
            <Pressable
              onPress={onClose}
              accessibilityRole="button"
              accessibilityLabel={closeLabel}
              style={styles.close}
              testID={testID ? `${testID}-close` : undefined}
            >
              <Text
                style={[textStyle('h2'), styles.closeGlyph]}
                importantForAccessibility="no"
                accessibilityElementsHidden
              >
                ×
              </Text>
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.body}>{children}</ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: 'flex-end' },
  scrim: { ...StyleSheet.absoluteFill, backgroundColor: colors.scrim },
  panel: {
    maxHeight: '90%',
    paddingTop: spacing.md,
    paddingHorizontal: spacing.lg,
    borderTopLeftRadius: radii.sheet,
    borderTopRightRadius: radii.sheet,
    backgroundColor: surfaces.sheet,
    shadowColor: shadows.sheet.color,
    shadowOpacity: shadows.sheet.opacity,
    shadowOffset: { width: 0, height: shadows.sheet.offsetY },
    shadowRadius: shadows.sheet.blur / 2,
    elevation: 16,
  },
  grab: {
    alignSelf: 'center',
    width: spacing.xxl + spacing.sm,
    height: spacing.xs,
    marginBottom: spacing.md,
    borderRadius: radii.pill,
    backgroundColor: colors.line,
  },
  header: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.md },
  title: { flex: 1, color: colors.ink },
  close: {
    width: minTapTarget,
    height: minTapTarget,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radii.pill,
    borderWidth: borderWidth.thin,
    borderColor: colors.control,
  },
  closeGlyph: { color: colors.ink },
  body: { gap: spacing.md },
});
