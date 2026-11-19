import type { MockLink } from '@apollo/client/testing';
import { MockedProvider } from '@apollo/client/testing/react';
import { TaskListingDocument } from '@taskbeavo/graphql';
import { catalogues, formatMoney, locales } from '@taskbeavo/i18n';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { GraphQLError } from 'graphql';
import { i18n } from '../src/i18n';
import { TaskDetailScreen } from '../src/screens/task-detail-screen';

const listing = {
  __typename: 'TaskListing',
  id: 't1',
  category: 'ERRANDS',
  title: 'Grocery run for Mama',
  description: 'Bread, milk and her prescription.',
  approximateLat: 42.66,
  approximateLng: 18.09,
  serviceAreaName: 'Dubrovnik',
  windowStart: null,
  windowEnd: null,
  flexibleDate: true,
  budgetCents: 2_000,
  photos: [{ __typename: 'TaskPhoto', id: 'p1', url: 'https://photos.example.com/p1.jpg' }],
  isMine: false,
  publishedAt: '2026-11-19T10:00:00Z',
};

function mock(extra: Record<string, unknown> = {}): MockLink.MockedResponse {
  return {
    request: { query: TaskListingDocument, variables: { id: 't1' } },
    result: { data: { taskListing: { ...listing, ...extra } } },
  };
}

async function show(mocks: MockLink.MockedResponse[]) {
  const onOpenOffers = jest.fn();
  await render(
    <MockedProvider mocks={mocks}>
      <TaskDetailScreen taskId="t1" onBack={jest.fn()} onOpenOffers={onOpenOffers} />
    </MockedProvider>,
  );
  return onOpenOffers;
}

describe('TaskDetailScreen', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it.each(locales)('shows the task with the approximate area only in %s', async (locale) => {
    await i18n.changeLanguage(locale);
    const messages = catalogues[locale];
    await show([mock()]);

    expect(await screen.findByTestId('task-detail')).toBeOnTheScreen();
    expect(screen.getByText('Grocery run for Mama')).toBeOnTheScreen();
    expect(screen.getByText(messages.enums.TaskCategory.ERRANDS)).toBeOnTheScreen();
    expect(screen.getByTestId('task-when')).toHaveTextContent(messages.taskListing.flexibleDate);
    expect(screen.getByTestId('task-area')).toHaveTextContent(
      messages.taskListing.area.replace('{area}', 'Dubrovnik'),
    );
    expect(screen.getByTestId('task-budget')).toHaveTextContent(
      messages.taskListing.budget.replace('{amount}', formatMoney(2_000, locale)),
    );
    expect(screen.getByTestId('task-approximate-note')).toHaveTextContent(
      messages.taskListing.approximateNote,
    );
    expect(
      screen.getByLabelText(messages.taskListing.photoAlt.replace('{number}', '1')),
    ).toBeOnTheScreen();
    // A helper or visitor has nothing to book or pay here.
    expect(screen.queryByTestId('see-offers')).toBeNull();
  });

  it('gives the poster their offers', async () => {
    await i18n.changeLanguage('en');
    const onOpenOffers = await show([mock({ isMine: true })]);

    await fireEvent.press(await screen.findByTestId('see-offers'));

    expect(onOpenOffers).toHaveBeenCalled();
  });

  it('says when the task is not available', async () => {
    await i18n.changeLanguage('en');
    await show([
      {
        request: { query: TaskListingDocument, variables: { id: 't1' } },
        result: {
          errors: [new GraphQLError('No such task.', { extensions: { code: 'TASK_NOT_FOUND' } })],
        },
      },
    ]);

    expect(await screen.findByTestId('task-detail-error')).toBeOnTheScreen();
    expect(screen.getByRole('alert')).toHaveTextContent(catalogues.en.errors.TASK_NOT_FOUND);
    // Nothing to retry: the task is gone or not public.
    expect(screen.queryByText(catalogues.en.taskListing.retry)).toBeNull();
  });

  it('offers a retry when loading fails', async () => {
    await i18n.changeLanguage('en');
    await show([
      { request: { query: TaskListingDocument, variables: { id: 't1' } }, error: new Error('x') },
      mock(),
    ]);

    await fireEvent.press(await screen.findByText(catalogues.en.taskListing.retry));

    expect(await screen.findByTestId('task-detail')).toBeOnTheScreen();
  });
});
