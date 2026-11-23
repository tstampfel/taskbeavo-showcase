import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import {
  ConversationDocument,
  ConversationMessagesDocument,
  typePolicies,
} from '@taskbeavo/graphql';
import { TASK_TIME_ZONE, catalogues, locales, type Locale } from '@taskbeavo/i18n';
import { NextIntlClientProvider } from 'next-intl';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { Observable } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { ConversationView } from './conversation-view';

vi.mock('../../i18n/navigation', () => ({
  Link: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

const ID = 'conv-1';

function conversation(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Conversation' as const,
    id: ID,
    taskId: 'task-1',
    taskTitle: 'Grocery run for Mama',
    myRole: 'POSTER' as const,
    counterpartName: 'Marko P.',
    stage: 'OFFER' as const,
    canSend: true,
    bookingId: null,
    lastMessageAt: null,
    ...extra,
  };
}

function message(id: string, extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Message' as const,
    id,
    conversationId: ID,
    body: `Message ${id}`,
    senderRole: 'HELPER' as const,
    isMine: false,
    masked: false,
    createdAt: '2026-11-06T17:02:00Z',
    ...extra,
  };
}

/** The page's first render from a cache that already holds the conversation (no network). */
function render(
  locale: Locale,
  { extra = {}, messages = [] }: { extra?: Record<string, unknown>; messages?: unknown[] } = {},
) {
  const link = new ApolloLink(() => new Observable(() => undefined));
  const client = new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
  client.cache.writeQuery({
    query: ConversationDocument,
    variables: { id: ID },
    data: { conversation: conversation(extra) } as never,
  });
  client.cache.writeQuery({
    query: ConversationMessagesDocument,
    variables: { conversationId: ID },
    data: { conversationMessages: messages } as never,
  });
  return renderToStaticMarkup(
    <ApolloProvider client={client}>
      <NextIntlClientProvider
        locale={locale}
        messages={catalogues[locale]}
        timeZone={TASK_TIME_ZONE}
      >
        <ConversationView conversationId={ID} locale={locale} />
      </NextIntlClientProvider>
    </ApolloProvider>,
  );
}

const escaped = (text: string) => text.replace(/'/g, '&#x27;').replace(/"/g, '&quot;');

describe('ConversationView', () => {
  it('shows the counterpart, the task, the masking note and the messages, oldest first', () => {
    const markup = render('en', {
      messages: [
        message('1', { body: 'Hvala Ana!' }),
        message('2', { body: 'Call me on •••', isMine: true, senderRole: 'POSTER', masked: true }),
      ],
    });

    expect(markup).toContain('>Marko P.</h2>');
    expect(markup).toContain('About: Grocery run for Mama');
    expect(markup).toContain('data-testid="conversation-stage-OFFER"');
    expect(markup).toContain(escaped(catalogues.en.chat.offerStage));
    expect(markup.indexOf('Hvala Ana!')).toBeLessThan(markup.indexOf('Call me on •••'));
    // The masked message says so; the sender is named for screen readers.
    expect(markup).toContain(catalogues.en.chat.masked);
    expect(markup).toContain('Marko P.: ');
    expect(markup).toContain('You: ');
    expect(markup).toContain('data-testid="conversation-input"');
    expect(markup).toContain('Message to Marko P.');
    expect(markup).not.toContain('View booking');
  });

  it('after acceptance links to the booking and drops the masking note', () => {
    const markup = render('en', { extra: { stage: 'BOOKED', bookingId: 'b-1' } });

    expect(markup).toContain('data-testid="conversation-stage-BOOKED"');
    expect(markup).toContain(escaped(catalogues.en.chat.bookedStage));
    expect(markup).not.toContain(escaped(catalogues.en.chat.offerStage));
    expect(markup).toContain('href="/bookings/b-1"');
  });

  it('a closed conversation stays readable with no way to write', () => {
    const markup = render('en', { extra: { canSend: false }, messages: [message('1')] });

    expect(markup).toContain('Message 1');
    expect(markup).toContain('data-testid="conversation-closed"');
    expect(markup).not.toContain('data-testid="conversation-input"');
  });

  it('an empty conversation invites the first message', () => {
    expect(render('en')).toContain(escaped(catalogues.en.chat.empty));
  });

  it.each(locales)('is translated in %s', (locale) => {
    const text = catalogues[locale].chat;
    const markup = render(locale, { messages: [message('1')] });

    expect(markup).toContain(escaped(text.offerStage));
    expect(markup).toContain(`>${text.send}</button>`);
  });
});
