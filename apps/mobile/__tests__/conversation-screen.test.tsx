import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { typePolicies } from '@taskbeavo/graphql';
import { catalogues, locales } from '@taskbeavo/i18n';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { GraphQLError } from 'graphql';
import { Observable } from 'rxjs';
import { i18n } from '../src/i18n';
import { ConversationScreen } from '../src/screens/conversation-screen';

const ID = 'conv-1';

function conversation(extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Conversation',
    id: ID,
    taskId: 'task-1',
    taskTitle: 'Grocery run for Mama',
    myRole: 'POSTER',
    counterpartName: 'Marko P.',
    stage: 'OFFER',
    canSend: true,
    bookingId: null,
    lastMessageAt: null,
    ...extra,
  };
}

function message(id: string, extra: Record<string, unknown> = {}) {
  return {
    __typename: 'Message',
    id,
    conversationId: ID,
    body: `Message ${id}`,
    senderRole: 'HELPER',
    isMine: false,
    masked: false,
    createdAt: '2026-11-06T17:02:00Z',
    ...extra,
  };
}

interface Options {
  conversation?: Record<string, unknown>;
  messages?: unknown[];
  send?: (body: string) => { data?: unknown; errors?: GraphQLError[] };
}

/** A client answering the conversation queries and SendMessage; MessageAdded stays silent. */
function client(options: Options, sent: string[]) {
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        const name = operation.operationName;
        if (name === 'MessageAdded') return;
        if (name === 'Conversation') {
          observer.next({ data: { conversation: conversation(options.conversation) } });
        } else if (name === 'ConversationMessages') {
          observer.next({ data: { conversationMessages: options.messages ?? [] } });
        } else if (name === 'SendMessage') {
          const body = String(operation.variables.body);
          sent.push(body);
          observer.next(
            (options.send?.(body) ?? {
              data: { sendMessage: message('sent', { body, isMine: true, senderRole: 'POSTER' }) },
            }) as never,
          );
        }
        observer.complete();
      }),
  );
  return new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
}

async function renderScreen(options: Options = {}) {
  const sent: string[] = [];
  const onOpenBooking = jest.fn();
  await render(
    <ApolloProvider client={client(options, sent)}>
      <ConversationScreen conversationId={ID} onOpenBooking={onOpenBooking} />
    </ApolloProvider>,
  );
  await screen.findByTestId('conversation');
  return { sent, onOpenBooking };
}

describe('ConversationScreen', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('shows the counterpart, the task, the masking note and the messages', async () => {
    await renderScreen({ messages: [message('1', { body: 'Hvala Ana!' })] });

    expect(screen.getByRole('header')).toHaveTextContent('Marko P.');
    expect(screen.getByText('About: Grocery run for Mama')).toBeOnTheScreen();
    expect(screen.getByTestId('conversation-stage-OFFER')).toHaveTextContent(
      catalogues.en.chat.offerStage,
    );
    expect(screen.getByText('Hvala Ana!')).toBeOnTheScreen();
    expect(screen.getByTestId('message-1').props.accessibilityLabel).toMatch(/^Marko P\., /);
  });

  it('sends a message and shows it, with the masking notice when the server masked it', async () => {
    const { sent } = await renderScreen({
      send: () => ({
        data: {
          sendMessage: message('m', { body: 'Call me on •••', isMine: true, masked: true }),
        },
      }),
    });

    await fireEvent.changeText(screen.getByTestId('conversation-input'), 'Call me on 091 234 5678');
    await fireEvent.press(screen.getByTestId('conversation-send'));

    expect(await screen.findByText('Call me on •••')).toBeOnTheScreen();
    expect(sent).toEqual(['Call me on 091 234 5678']);
    expect(screen.getByTestId('conversation-masked')).toHaveTextContent(
      catalogues.en.contactFilter.masked,
    );
    expect(screen.getByTestId('conversation-input').props.value).toBe('');
  });

  it.each(locales)('translates a refused message in %s', async (locale) => {
    await i18n.changeLanguage(locale);
    await renderScreen({
      send: () => ({
        errors: [new GraphQLError('refused', { extensions: { code: 'MESSAGE_CONTACT_DETAILS' } })],
      }),
    });

    await fireEvent.changeText(screen.getByTestId('conversation-input'), '091\n234\n5678');
    await fireEvent.press(screen.getByTestId('conversation-send'));

    await waitFor(() =>
      expect(screen.getByTestId('conversation-input-error')).toHaveTextContent(
        catalogues[locale].errors.MESSAGE_CONTACT_DETAILS,
      ),
    );
  });

  it('after acceptance links to the booking and drops the masking note', async () => {
    const { onOpenBooking } = await renderScreen({
      conversation: { stage: 'BOOKED', bookingId: 'b-1' },
    });

    expect(screen.getByTestId('conversation-stage-BOOKED')).toHaveTextContent(
      catalogues.en.chat.bookedStage,
    );
    await fireEvent.press(screen.getByText('View booking'));
    expect(onOpenBooking).toHaveBeenCalledWith('b-1');
  });

  it('a closed conversation stays readable with no way to write', async () => {
    await renderScreen({ conversation: { canSend: false }, messages: [message('1')] });

    expect(screen.getByText('Message 1')).toBeOnTheScreen();
    expect(screen.getByTestId('conversation-closed')).toHaveTextContent(catalogues.en.chat.closed);
    expect(screen.queryByTestId('conversation-input')).toBeNull();
  });

  it('a blank message is not sent', async () => {
    const { sent } = await renderScreen();

    await fireEvent.changeText(screen.getByTestId('conversation-input'), '   ');
    await fireEvent.press(screen.getByTestId('conversation-send'));

    expect(sent).toEqual([]);
  });
});
