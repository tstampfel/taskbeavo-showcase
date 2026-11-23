'use client';

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
} from '@taskbeavo/graphql';
import { formatDateTime, type Locale } from '@taskbeavo/i18n';
import { useTranslations } from 'next-intl';
import { useEffect, useId, useState, type FormEvent } from 'react';
import { Link } from '../../i18n/navigation';
import { Button, InfoIcon, LockIcon, Note } from '../ui';
import styles from './chat.module.css';

/**
 * The private chat of a poster and one helper (BEAV-104, the chat part of mockup 5a). Before a
 * booking is paid the server masks contact details in new messages and refuses doubtful ones
 * (MESSAGE_CONTACT_DETAILS); the page says so above the messages. Live through messageAdded,
 * refetched when the tab becomes visible again and when the socket reconnects
 * (src/apollo/client.ts), since missed events aren't replayed.
 */
export function ConversationView({
  conversationId,
  locale,
}: {
  conversationId: string;
  locale: Locale;
}) {
  const t = useTranslations();
  const inputId = useId();
  const conversationQuery = useQuery(ConversationDocument, {
    variables: { id: conversationId },
    fetchPolicy: 'cache-and-network',
  });
  const messagesQuery = useQuery(ConversationMessagesDocument, {
    variables: { conversationId },
    fetchPolicy: 'cache-and-network',
  });
  const { subscribeToMore, fetchMore, refetch } = messagesQuery;
  useEffect(
    () => subscribeToConversation(subscribeToMore, conversationId),
    [subscribeToMore, conversationId],
  );
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refetch().catch(() => undefined);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [refetch]);
  const [send, sending] = useMutation(SendMessageDocument);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [lastMasked, setLastMasked] = useState(false);
  const [noEarlier, setNoEarlier] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);

  const conversation = conversationQuery.data?.conversation;
  const messages = messagesQuery.data?.conversationMessages;
  const maskedLabel = t('chat.masked');

  if (!conversation || !messages) {
    const failed =
      (conversationQuery.error || messagesQuery.error) &&
      !conversationQuery.loading &&
      !messagesQuery.loading;
    if (!failed) return null;
    return (
      <div className={styles.section} data-testid="conversation-error">
        <p className={styles.error} role="alert">
          {t('chat.loadError')}
        </p>
        <Button
          variant="secondary"
          onClick={() => {
            void conversationQuery.refetch().catch(() => undefined);
            void messagesQuery.refetch().catch(() => undefined);
          }}
        >
          {t('chat.retry')}
        </Button>
      </div>
    );
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault();
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
      setError(code && t.has(`errors.${code}`) ? t(`errors.${code}`) : t('chat.sendError'));
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

  return (
    <div className={styles.section} data-testid="conversation">
      <div className={styles.head}>
        <h2 className={styles.name}>{conversation.counterpartName}</h2>
        <p className={styles.muted}>{t('chat.aboutTask', { task: conversation.taskTitle })}</p>
        {conversation.bookingId ? (
          <Link href={`/bookings/${conversation.bookingId}`} className={styles.link}>
            {t('chat.viewBooking')}
          </Link>
        ) : null}
      </div>
      <Note
        tone="warn"
        icon={<LockIcon />}
        data-testid={`conversation-stage-${conversation.stage}`}
      >
        {t(conversation.stage === 'OFFER' ? 'chat.offerStage' : 'chat.bookedStage')}
      </Note>
      {messages.length >= MESSAGE_PAGE && !noEarlier ? (
        <Button
          variant="secondary"
          size="sm"
          disabled={loadingEarlier}
          aria-busy={loadingEarlier}
          onClick={() => void loadEarlier()}
        >
          {t('chat.earlier')}
        </Button>
      ) : null}
      {messages.length === 0 ? (
        <p className={styles.muted}>{t('chat.empty')}</p>
      ) : (
        <ol className={styles.messages} aria-live="polite">
          {messages.map((message) => {
            const time = formatDateTime(String(message.createdAt), locale, 'short');
            const sender = message.isMine ? t('chat.you') : conversation.counterpartName;
            return (
              <li
                key={message.id}
                className={`${styles.bubble} ${message.isMine ? styles.mine : styles.theirs}`}
                data-testid={`message-${message.id}`}
              >
                <span className={styles.visuallyHidden}>{`${sender}: `}</span>
                <p className={styles.text}>{message.body}</p>
                <p className={styles.meta}>{message.masked ? `${time} · ${maskedLabel}` : time}</p>
              </li>
            );
          })}
        </ol>
      )}
      {lastMasked ? (
        <Note icon={<InfoIcon />} data-testid="conversation-masked">
          {t('contactFilter.masked')}
        </Note>
      ) : null}
      {conversation.canSend ? (
        <form className={styles.compose} onSubmit={(event) => void submit(event)}>
          {/* Once messages are no longer masked: codes belong in Access details, which are
              hidden after the booking ends, while the chat stays readable (owner decision). Only
          the poster can edit Access details, so only the poster sees it (review R3-1). */}
          {conversation.stage === 'BOOKED' && conversation.myRole === 'POSTER' ? (
            <Note icon={<LockIcon />} data-testid="conversation-access-codes">
              {t('chat.accessCodesNote')}
            </Note>
          ) : null}
          <label htmlFor={inputId} className={styles.label}>
            {t('chat.inputLabel', { name: conversation.counterpartName })}
          </label>
          <textarea
            id={inputId}
            className={styles.input}
            value={draft}
            rows={3}
            maxLength={MESSAGE_MAX + 100}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${inputId}-error` : undefined}
            onChange={(event) => {
              setDraft(event.target.value);
              if (error) setError(null);
            }}
            data-testid="conversation-input"
          />
          {error ? (
            <p id={`${inputId}-error`} className={styles.error} role="alert">
              {error}
            </p>
          ) : null}
          <Button
            type="submit"
            disabled={sending.loading || messageProblem(draft) !== null}
            aria-busy={sending.loading}
            data-testid="conversation-send"
          >
            {t('chat.send')}
          </Button>
        </form>
      ) : (
        <Note icon={<InfoIcon />} data-testid="conversation-closed">
          {t('chat.closed')}
        </Note>
      )}
    </div>
  );
}
