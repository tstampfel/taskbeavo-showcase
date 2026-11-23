import { useMutation, useQuery } from '@apollo/client/react';
import {
  ConversationDocument,
  ConversationMessagesDocument,
  MESSAGE_MAX,
  MESSAGE_PAGE,
  SendMessageDocument,
  messageProblem,
  signInErrorOf,
  subscribeToConversation,
  withEarlierMessages,
  withMessage,
  type ChatMessage,
} from '@taskbeavo/graphql';
import { defaultLocale, formatDateTime, isLocale, type Locale } from '@taskbeavo/i18n';
import { borderWidth, colors, radii, spacing } from '@taskbeavo/ui-tokens';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ActivityIndicator, StyleSheet, View } from 'react-native';
import { i18n } from '../i18n';
import { Button, Input, Note, Screen } from '../components/ui';
import { Text } from '../components/ui/text';
import { textStyle } from '../components/ui/text-style';

export interface ConversationScreenProps {
  conversationId: string;
  /** Opens the pair's booking, once there is one. */
  onOpenBooking(bookingId: string): void;
  /** The top bar's back button. */
  onBack?: () => void;
}

/**
 * The private chat of a poster and one helper (BEAV-104, the chat part of mockup 5a). Before
 * a booking is paid the server masks contact details in new messages and refuses doubtful
 * ones (MESSAGE_CONTACT_DETAILS); the screen says so above the messages. Live through
 * messageAdded; the app refetches on return to the foreground and on reconnect. Message text
 * stays in memory only (NOT_PERSISTED). Expo shows no banner for a push while the app is in
 * the foreground, so an open conversation isn't interrupted by its own messages.
 */
export function ConversationScreen({
  conversationId,
  onOpenBooking,
  onBack,
}: ConversationScreenProps) {
  const { t, i18n: active } = useTranslation();
  const locale: Locale = isLocale(active.language) ? active.language : defaultLocale;
  const conversationQuery = useQuery(ConversationDocument, {
    variables: { id: conversationId },
    fetchPolicy: 'cache-and-network',
  });
  const messagesQuery = useQuery(ConversationMessagesDocument, {
    variables: { conversationId },
    fetchPolicy: 'cache-and-network',
  });
  const { subscribeToMore, fetchMore } = messagesQuery;
  useEffect(
    () => subscribeToConversation(subscribeToMore, conversationId),
    [subscribeToMore, conversationId],
  );
  const [send, sending] = useMutation(SendMessageDocument);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [lastMasked, setLastMasked] = useState(false);
  const [noEarlier, setNoEarlier] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);

  const conversation = conversationQuery.data?.conversation;
  const messages = messagesQuery.data?.conversationMessages;

  if (!conversation || !messages) {
    const failed =
      (conversationQuery.error || messagesQuery.error) &&
      !conversationQuery.loading &&
      !messagesQuery.loading;
    return (
      <Screen
        title={t('chat.metaTitle')}
        onBack={onBack}
        backLabel={t('chat.back')}
        testID={failed ? 'conversation-error' : 'conversation-loading'}
      >
        {failed ? (
          <View style={styles.center}>
            <Text accessibilityRole="alert" style={[textStyle('body'), styles.ink]}>
              {t('chat.loadError')}
            </Text>
            <Button
              variant="secondary"
              label={t('chat.retry')}
              onPress={() => {
                void conversationQuery.refetch().catch(() => undefined);
                void messagesQuery.refetch().catch(() => undefined);
              }}
            />
          </View>
        ) : (
          <ActivityIndicator color={colors.green} accessibilityLabel={t('chat.metaTitle')} />
        )}
      </Screen>
    );
  }

  const submit = async () => {
    const problem = messageProblem(draft);
    if (problem) {
      setError(problem === 'tooLong' ? t('chat.tooLong', { max: MESSAGE_MAX }) : null);
      return;
    }
    try {
      const result = await send({
        variables: { conversationId, body: draft },
        update(cache, { data }) {
          const sent = data?.sendMessage;
          if (!sent) return;
          cache.updateQuery(
            { query: ConversationMessagesDocument, variables: { conversationId } },
            (previous) =>
              previous && {
                ...previous,
                conversationMessages: withMessage(previous.conversationMessages, sent),
              },
          );
        },
      });
      setDraft('');
      setError(null);
      setLastMasked(result.data?.sendMessage.masked ?? false);
    } catch (failure) {
      const { code } = signInErrorOf(failure);
      setError(code && i18n.exists(`errors.${code}`) ? t(`errors.${code}`) : t('chat.sendError'));
      // The conversation may have closed meanwhile: show what it is now.
      void conversationQuery.refetch().catch(() => undefined);
    }
  };

  const loadEarlier = async () => {
    const first = messages[0];
    if (!first) return;
    setLoadingEarlier(true);
    try {
      const result = await fetchMore({
        variables: { conversationId, before: first.id },
        updateQuery: (previous, { fetchMoreResult }) =>
          withEarlierMessages(previous, fetchMoreResult),
      });
      if (result.data.conversationMessages.length < MESSAGE_PAGE) setNoEarlier(true);
    } catch {
      setError(t('chat.loadError'));
    } finally {
      setLoadingEarlier(false);
    }
  };

  const footer = conversation.canSend ? (
    <View style={styles.compose}>
      {/* Once messages are no longer masked: codes belong in Access details, which are
          hidden after the booking ends, while the chat stays readable (owner decision). */}
      {conversation.stage === 'BOOKED' ? (
        <Note testID="conversation-access-codes">{t('chat.accessCodesNote')}</Note>
      ) : null}
      <Input
        label={t('chat.inputLabel', { name: conversation.counterpartName })}
        value={draft}
        onChangeText={(text) => {
          setDraft(text);
          if (error) setError(null);
        }}
        multiline
        maxLength={MESSAGE_MAX + 100}
        error={error ?? undefined}
        testID="conversation-input"
      />
      <Button
        label={t('chat.send')}
        size="sm"
        busy={sending.loading}
        disabled={messageProblem(draft) !== null}
        onPress={() => void submit()}
        testID="conversation-send"
      />
    </View>
  ) : (
    <Note testID="conversation-closed">{t('chat.closed')}</Note>
  );

  return (
    <Screen
      title={conversation.counterpartName}
      onBack={onBack}
      backLabel={t('chat.back')}
      testID="conversation"
      footer={footer}
    >
      <Text style={[textStyle('caption'), styles.muted]}>
        {t('chat.aboutTask', { task: conversation.taskTitle })}
      </Text>
      <Note tone="warn" testID={`conversation-stage-${conversation.stage}`}>
        {t(conversation.stage === 'OFFER' ? 'chat.offerStage' : 'chat.bookedStage')}
      </Note>
      {conversation.bookingId ? (
        <Button
          variant="ghost"
          size="sm"
          label={t('chat.viewBooking')}
          onPress={() => onOpenBooking(conversation.bookingId!)}
        />
      ) : null}
      {messages.length >= MESSAGE_PAGE && !noEarlier ? (
        <Button
          variant="secondary"
          size="sm"
          label={t('chat.earlier')}
          busy={loadingEarlier}
          onPress={() => void loadEarlier()}
          testID="conversation-earlier"
        />
      ) : null}
      {messages.length === 0 ? (
        <Text style={[textStyle('body'), styles.muted]}>{t('chat.empty')}</Text>
      ) : (
        messages.map((message) => (
          <Bubble
            key={message.id}
            message={message}
            sender={message.isMine ? t('chat.you') : conversation.counterpartName}
            time={formatDateTime(String(message.createdAt), locale, 'short')}
          />
        ))
      )}
      {lastMasked ? <Note testID="conversation-masked">{t('contactFilter.masked')}</Note> : null}
    </Screen>
  );
}

function Bubble({ message, sender, time }: { message: ChatMessage; sender: string; time: string }) {
  const { t } = useTranslation();
  const mine = message.isMine;
  const masked = t('chat.masked');
  return (
    <View
      accessible
      accessibilityLabel={t('chat.messageLabel', { sender, time, body: message.body })}
      style={[styles.bubble, mine ? styles.mine : styles.theirs]}
      testID={`message-${message.id}`}
    >
      <Text style={[textStyle('body'), mine ? styles.mineText : styles.ink]}>{message.body}</Text>
      <Text style={[textStyle('caption'), mine ? styles.mineMeta : styles.muted]}>
        {message.masked ? `${time} · ${masked}` : time}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { gap: spacing.lg, alignItems: 'stretch' },
  compose: { gap: spacing.sm },
  bubble: { maxWidth: '85%', padding: spacing.md, borderRadius: radii.lg, gap: spacing.xs },
  mine: { alignSelf: 'flex-end', backgroundColor: colors.green },
  theirs: {
    alignSelf: 'flex-start',
    backgroundColor: colors.white,
    borderWidth: borderWidth.thin,
    borderColor: colors.line,
  },
  mineText: { color: colors.white },
  mineMeta: { color: colors.white },
  ink: { color: colors.ink },
  muted: { color: colors.inkMuted },
});
