import type { SubscribeToMoreFunction } from '@apollo/client';
import {
  MessageAddedDocument,
  type ConversationMessagesQuery,
  type ConversationMessagesQueryVariables,
  type MessageAddedSubscription,
} from './generated/graphql';

export type ChatMessage = ConversationMessagesQuery['conversationMessages'][number];

/** The server's limit on a message (chat/services.py MAX_MESSAGE). */
export const MESSAGE_MAX = 2000;
/** Messages per page of ConversationMessages. */
export const MESSAGE_PAGE = 50;

export type MessageProblem = 'empty' | 'tooLong';

/** Why `text` can't be sent, or null when it can: the server trims it and checks the same. */
export function messageProblem(text: string): MessageProblem | null {
  const trimmed = text.trim();
  if (!trimmed) return 'empty';
  if (trimmed.length > MESSAGE_MAX) return 'tooLong';
  return null;
}

/**
 * `messages` with `added` at the end (the list is oldest first), unless a message with its ID
 * is already there: a redelivered event, the sender's own mutation result, or a refetch that
 * raced the event. Never changes the input.
 */
export function withMessage<T extends { id: string }>(messages: readonly T[], added: T): T[] {
  if (messages.some((message) => message.id === added.id)) return [...messages];
  return [...messages, added];
}

/** `updateQuery` for subscribeToMore on ConversationMessages with MessageAdded. */
export function withNewMessage(
  previous: ConversationMessagesQuery,
  { subscriptionData }: { subscriptionData: { data?: MessageAddedSubscription | null } },
): ConversationMessagesQuery {
  const added = subscriptionData.data?.messageAdded;
  if (!added || previous.conversationMessages.some((message) => message.id === added.id)) {
    return previous;
  }
  return { ...previous, conversationMessages: [...previous.conversationMessages, added] };
}

/**
 * An older page (from fetchMore with `before`) put in front of the messages already loaded,
 * skipping any already there.
 */
export function withEarlierMessages(
  previous: ConversationMessagesQuery,
  earlier: ConversationMessagesQuery,
): ConversationMessagesQuery {
  const known = new Set(previous.conversationMessages.map((message) => message.id));
  const fresh = earlier.conversationMessages.filter((message) => !known.has(message.id));
  return { ...previous, conversationMessages: [...fresh, ...previous.conversationMessages] };
}

/**
 * Keeps an open conversation live: each new message, the viewer's own from other devices
 * included, is appended (`withNewMessage`). Returns the unsubscribe function.
 */
export function subscribeToConversation(
  subscribeToMore: SubscribeToMoreFunction<
    ConversationMessagesQuery,
    ConversationMessagesQueryVariables
  >,
  conversationId: string,
): () => void {
  return subscribeToMore({
    document: MessageAddedDocument,
    variables: { conversationId },
    updateQuery: (_unsafe, options) =>
      options.complete ? withNewMessage(options.previousData, options) : undefined,
  });
}
