import { describe, expect, it, vi } from 'vitest';
import {
  MESSAGE_MAX,
  messageProblem,
  subscribeToConversation,
  withEarlierMessages,
  withMessage,
  withNewMessage,
  type ChatMessage,
} from './chat';
import { MessageAddedDocument, type ConversationMessagesQuery } from './generated/graphql';

function message(id: string, createdAt = '2026-11-07T08:00:00Z'): ChatMessage {
  return {
    id,
    conversationId: 'c1',
    body: `Body ${id}`,
    senderRole: 'HELPER',
    isMine: false,
    masked: false,
    createdAt,
  };
}

const page = (...ids: string[]): ConversationMessagesQuery => ({
  conversationMessages: ids.map((id) => message(id)),
});

describe('messageProblem', () => {
  it('refuses blank and overlong text, as the server does after trimming', () => {
    expect(messageProblem('   ')).toBe('empty');
    expect(messageProblem('x'.repeat(MESSAGE_MAX + 1))).toBe('tooLong');
    expect(messageProblem(` ${'x'.repeat(MESSAGE_MAX)} `)).toBeNull();
    expect(messageProblem('Hvala!')).toBeNull();
  });
});

describe('withNewMessage', () => {
  it('appends a new message at the end', () => {
    const next = withNewMessage(page('1'), {
      subscriptionData: { data: { messageAdded: message('2') } },
    });
    expect(next.conversationMessages.map((m) => m.id)).toEqual(['1', '2']);
  });

  it('ignores a message already in the list and an empty event', () => {
    const previous = page('1', '2');
    expect(
      withNewMessage(previous, { subscriptionData: { data: { messageAdded: message('2') } } }),
    ).toBe(previous);
    expect(withNewMessage(previous, { subscriptionData: { data: null } })).toBe(previous);
  });
});

describe('withMessage', () => {
  it('adds once and never mutates the input', () => {
    const list = [message('1')];
    expect(withMessage(list, message('2')).map((m) => m.id)).toEqual(['1', '2']);
    expect(withMessage(list, message('1')).map((m) => m.id)).toEqual(['1']);
    expect(list).toHaveLength(1);
  });
});

describe('withEarlierMessages', () => {
  it('puts an older page in front, skipping duplicates', () => {
    const next = withEarlierMessages(page('3', '4'), page('1', '2', '3'));
    expect(next.conversationMessages.map((m) => m.id)).toEqual(['1', '2', '3', '4']);
  });
});

describe('subscribeToConversation', () => {
  it('subscribes to the conversation and appends complete results', () => {
    const stop = vi.fn();
    const subscribeToMore = vi.fn().mockReturnValue(stop);

    const unsubscribe = subscribeToConversation(subscribeToMore, 'c1');

    const options = subscribeToMore.mock.calls[0]![0];
    expect(options.document).toBe(MessageAddedDocument);
    expect(options.variables).toEqual({ conversationId: 'c1' });
    const update = options.updateQuery(undefined, {
      complete: true,
      previousData: page('1'),
      subscriptionData: { data: { messageAdded: message('2') } },
    });
    expect(update.conversationMessages.map((m: ChatMessage) => m.id)).toEqual(['1', '2']);
    expect(options.updateQuery(undefined, { complete: false })).toBeUndefined();
    unsubscribe();
    expect(stop).toHaveBeenCalled();
  });
});
