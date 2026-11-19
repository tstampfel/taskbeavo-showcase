import { InMemoryCache } from '@apollo/client';
import type { MockLink } from '@apollo/client/testing';
import { MockedProvider } from '@apollo/client/testing/react';
import { FeedHelperProfileDocument, TaskFeedDocument, typePolicies } from '@taskbeavo/graphql';
import { GraphQLError } from 'graphql';
import { catalogues, formatMoney, locales } from '@taskbeavo/i18n';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { i18n } from '../src/i18n';
import { TaskFeedScreen } from '../src/screens/task-feed-screen';

function listing(id: string, extra: Record<string, unknown> = {}) {
  return {
    __typename: 'TaskListing',
    id,
    category: 'GARDEN',
    title: `Task ${id}`,
    description: 'Mow the lawn.',
    approximateLat: 42.66,
    approximateLng: 18.09,
    serviceAreaName: 'Dubrovnik',
    windowStart: '2026-11-22T08:00:00Z',
    windowEnd: '2026-11-22T10:00:00Z',
    flexibleDate: false,
    budgetCents: 3_500,
    photos: [],
    isMine: false,
    publishedAt: '2026-11-19T10:00:00Z',
    ...extra,
  };
}

function feedMock(
  variables: Record<string, unknown>,
  ids: string[],
  endCursor: string | null,
  hasMore: boolean,
): MockLink.MockedResponse {
  return {
    request: { query: TaskFeedDocument, variables },
    result: {
      data: {
        taskFeed: {
          __typename: 'TaskListingPage',
          id: `feed:${String(variables.category ?? '')}:${String(variables.after ?? '')}`,
          endCursor,
          hasMore,
          items: ids.map((id) => listing(id)),
        },
      },
    },
  };
}

const FIRST = { first: 20, category: null };

function helperMock(hasProfile: boolean): MockLink.MockedResponse {
  return {
    request: { query: FeedHelperProfileDocument },
    result: {
      data: {
        myHelperProfile: hasProfile ? { __typename: 'HelperProfile', id: 'hp1' } : null,
      },
    },
    maxUsageCount: 5,
  };
}

async function show(
  mocks: MockLink.MockedResponse[],
  onOpenTask = jest.fn(),
  { helper = true, onPostTask }: { helper?: boolean; onPostTask?: () => void } = {},
) {
  await render(
    <MockedProvider
      mocks={[helperMock(helper), ...mocks]}
      cache={new InMemoryCache({ typePolicies })}
    >
      <TaskFeedScreen
        firstName="Luka"
        onOpenTask={onOpenTask}
        onOpenSettings={jest.fn()}
        onPostTask={onPostTask}
      />
    </MockedProvider>,
  );
  return onOpenTask;
}

describe('TaskFeedScreen', () => {
  afterAll(async () => {
    await i18n.changeLanguage('hr');
  });

  it.each(locales)('lists open tasks with the approximate area in %s', async (locale) => {
    await i18n.changeLanguage(locale);
    const messages = catalogues[locale];
    await show([feedMock(FIRST, ['a', 'b'], 'c1', false)]);

    expect(await screen.findByTestId('feed-content')).toBeOnTheScreen();
    expect(screen.getByText('Task a')).toBeOnTheScreen();
    expect(
      screen.getAllByText(new RegExp(messages.taskListing.area.replace('{area}', 'Dubrovnik'))),
    ).toHaveLength(2);
    const budget = messages.taskListing.budget.replace('{amount}', formatMoney(3_500, locale));
    expect(screen.getAllByText(budget)).toHaveLength(2);
    // Rule k: the screen says the order is the same for everyone.
    expect(screen.getByTestId('feed-order')).toHaveTextContent(messages.taskFeed.order);
    expect(screen.getByRole('header')).toHaveTextContent(/Luka/);
  });

  it('opens the post-a-task wizard from home', async () => {
    await i18n.changeLanguage('en');
    const onPostTask = jest.fn();
    await show([feedMock(FIRST, [], null, false)], jest.fn(), { onPostTask });

    await fireEvent.press(await screen.findByText(catalogues.en.postTask.open));
    expect(onPostTask).toHaveBeenCalled();
  });

  it('shows an empty state', async () => {
    await i18n.changeLanguage('en');
    await show([feedMock(FIRST, [], null, false)]);

    expect(await screen.findByTestId('feed-empty')).toHaveTextContent(
      catalogues.en.taskFeed.empty,
      { exact: false },
    );
  });

  it('tells a poster without a helper profile how open tasks appear', async () => {
    await i18n.changeLanguage('en');
    await show([feedMock(FIRST, [], null, false)], jest.fn(), { helper: false });

    const empty = await screen.findByTestId('feed-not-helper');
    expect(empty).toHaveTextContent(catalogues.en.taskFeed.notHelperHint, { exact: false });
    expect(screen.queryByText(catalogues.en.taskFeed.emptyHint)).toBeNull();
  });

  it('says so when the next page fails, and keeps the tasks', async () => {
    await i18n.changeLanguage('en');
    await show([
      feedMock(FIRST, ['a'], 'c1', true),
      {
        request: { query: TaskFeedDocument, variables: { first: 20, category: null, after: 'c1' } },
        error: new Error('offline'),
      },
    ]);

    await fireEvent.press(await screen.findByTestId('feed-more'));

    expect(await screen.findByTestId('feed-more-error')).toHaveTextContent(
      catalogues.en.taskFeed.loadMoreError,
    );
    expect(screen.getByText('Task a')).toBeOnTheScreen();
  });

  it('starts again from the top when the cursor is no longer valid', async () => {
    await i18n.changeLanguage('en');
    await show([
      feedMock(FIRST, ['a'], 'c1', true),
      {
        request: { query: TaskFeedDocument, variables: { first: 20, category: null, after: 'c1' } },
        result: {
          errors: [new GraphQLError('Bad cursor.', { extensions: { code: 'INVALID_CURSOR' } })],
        },
      },
      feedMock(FIRST, ['n', 'a'], 'c2', false),
    ]);

    await fireEvent.press(await screen.findByTestId('feed-more'));

    expect(await screen.findByText('Task n')).toBeOnTheScreen();
    expect(screen.queryByTestId('feed-more-error')).toBeNull();
  });

  it('shows an error with a retry', async () => {
    await i18n.changeLanguage('en');
    await show([
      { request: { query: TaskFeedDocument, variables: FIRST }, error: new Error('offline') },
      feedMock(FIRST, ['a'], 'c1', false),
    ]);

    expect(await screen.findByTestId('feed-error')).toBeOnTheScreen();
    await fireEvent.press(screen.getByText(catalogues.en.taskFeed.retry));
    expect(await screen.findByText('Task a')).toBeOnTheScreen();
  });

  it('appends the next page', async () => {
    await i18n.changeLanguage('en');
    await show([
      feedMock(FIRST, ['a', 'b'], 'c1', true),
      feedMock({ first: 20, category: null, after: 'c1' }, ['c'], null, false),
    ]);

    await fireEvent.press(await screen.findByTestId('feed-more'));

    expect(await screen.findByText('Task c')).toBeOnTheScreen();
    expect(screen.getByText('Task a')).toBeOnTheScreen();
    expect(screen.queryByTestId('feed-more')).toBeNull();
  });

  it('filters by category', async () => {
    await i18n.changeLanguage('en');
    await show([
      feedMock(FIRST, ['a'], 'c1', false),
      feedMock({ first: 20, category: 'ERRANDS' }, ['e'], 'c2', false),
    ]);
    await screen.findByText('Task a');

    await fireEvent.press(screen.getByTestId('feed-category-ERRANDS'));

    expect(await screen.findByText('Task e')).toBeOnTheScreen();
    expect(screen.getByTestId('feed-category-ERRANDS')).toBeChecked();
  });

  it('opens a task', async () => {
    await i18n.changeLanguage('en');
    const onOpenTask = await show([feedMock(FIRST, ['a'], null, false)]);

    await fireEvent.press(await screen.findByTestId('feed-task-a'));

    expect(onOpenTask).toHaveBeenCalledWith('a');
  });

  it('keeps every tap target at least 44 points', async () => {
    await i18n.changeLanguage('en');
    await show([feedMock(FIRST, ['a'], null, false)]);
    await screen.findByText('Task a');
    for (const chip of screen.getAllByRole('radio')) {
      expect(chip).toHaveStyle({ minHeight: 44 });
    }
  });
});
