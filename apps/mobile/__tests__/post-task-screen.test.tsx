import { ApolloClient, ApolloLink, InMemoryCache } from '@apollo/client';
import { ApolloProvider } from '@apollo/client/react';
import { typePolicies } from '@taskbeavo/graphql';
import { catalogues, type Locale } from '@taskbeavo/i18n';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { Observable } from 'rxjs';
import { i18n } from '../src/i18n';
import { PostTaskScreen } from '../src/screens/post-task-screen';
import type { AddressSearch } from '../src/tasks/address-search';
import { emptyDraft, type TaskDraft } from '../src/tasks/post-task-model';
import { createTaskDraftStore, type KeyValueStore } from '../src/tasks/task-draft-store';

const metrics = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 47, left: 0, right: 0, bottom: 34 },
};
// Thursday 19 November 2026, 10:00 in Zagreb.
const NOW = new Date('2026-11-19T09:00:00Z');

type Answer = Record<string, unknown> | { error: string };

function client(answers: Record<string, () => Answer>, variables: Record<string, unknown>) {
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        const name = operation.operationName ?? '';
        variables[name] = operation.variables;
        const answer = answers[name]?.();
        if (!answer) throw new Error(`No answer for ${name}`);
        if ('error' in answer) {
          observer.next({ errors: [{ message: 'no', extensions: { code: answer.error } }] });
        } else {
          observer.next({ data: answer });
        }
        observer.complete();
      }),
  );
  return new ApolloClient({ cache: new InMemoryCache({ typePolicies }), link });
}

function memory(): KeyValueStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: async (key) => data.get(key) ?? null,
    setItem: async (key, value) => void data.set(key, value),
    removeItem: async (key) => void data.delete(key),
  };
}

const VIEWER = {
  __typename: 'User',
  id: 'user-1',
  email: 'ana@example.com',
  firstName: 'Ana',
  lastName: 'Horvat',
  locale: 'en',
  country: 'AT',
  timeZone: 'Europe/Vienna',
  phoneVerified: true,
  phoneNumberMasked: null,
};

const CONTACT = {
  __typename: 'CareContact',
  id: 'contact-1',
  name: 'Mama Kata',
  phone: '+385981234567',
  smsCapable: true,
  area: 'Gruž',
  language: 'HR',
  noSmartphone: true,
  createdAt: '2026-11-01T10:00:00Z',
  updatedAt: '2026-11-01T10:00:00Z',
};

const POSTED = {
  postTask: {
    __typename: 'TaskPayload',
    id: 'task-9',
    warnings: [],
    task: {
      __typename: 'Task',
      id: 'task-9',
      status: 'OPEN',
      category: 'ERRANDS',
      title: 'Grocery run',
      description: 'Bread and milk.',
      approximateLat: 42.66,
      approximateLng: 18.08,
      serviceAreaName: 'Dubrovnik',
      windowStart: '2026-11-20T07:00:00Z',
      windowEnd: '2026-11-20T11:00:00Z',
      flexibleDate: false,
      budgetCents: 3_000,
      photos: [],
      isMine: true,
      createdAt: '2026-11-19T09:00:00Z',
      publishedAt: '2026-11-19T09:00:00Z',
      address: 'Ulica Ante Starčevića 1, Dubrovnik',
      lat: 42.6583,
      lng: 18.0861,
      accessDetails: null,
      onSiteContact: null,
      careContact: null,
      textMasked: false,
      holdReasons: null,
    },
  },
};

function fakeSearch(fail = false): AddressSearch & { search: jest.Mock } {
  return {
    attribution: '© OpenStreetMap contributors',
    search: jest.fn(async () => {
      if (fail) throw new Error('offline');
      return [
        {
          id: '7',
          address: 'Ulica Ante Starčevića 1, Dubrovnik',
          lat: 42.6583,
          lng: 18.0861,
          city: 'Dubrovnik',
        },
      ];
    }),
  };
}

async function renderWizard({
  answers = {},
  plain = memory(),
  secure = memory(),
  addressSearch = fakeSearch(),
}: {
  answers?: Record<string, () => Answer>;
  plain?: ReturnType<typeof memory>;
  secure?: ReturnType<typeof memory>;
  addressSearch?: AddressSearch;
} = {}) {
  const variables: Record<string, unknown> = {};
  const props = {
    onClose: jest.fn(),
    onPosted: jest.fn(),
    onVerifyPhone: jest.fn(),
    onAcceptTerms: jest.fn(),
  };
  const all = {
    Viewer: () => ({ viewer: VIEWER }),
    MyCareContacts: () => ({ myCareContacts: [CONTACT] }),
    ...answers,
  };
  const store = createTaskDraftStore(plain, secure);
  await render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ApolloProvider client={client(all, variables)}>
        <PostTaskScreen
          {...props}
          draftStore={store}
          addressSearch={addressSearch}
          now={() => NOW}
        />
      </ApolloProvider>
    </SafeAreaProvider>,
  );
  return { variables, props, plain, secure, store };
}

/** Every text the screen renders, labels and hints included, without styles. */
function shownText(): string {
  const out: string[] = [];
  const walk = (node: unknown) => {
    if (typeof node === 'string') out.push(node);
    else if (Array.isArray(node)) node.forEach(walk);
    else if (node && typeof node === 'object') {
      const { props, children } = node as { props?: Record<string, unknown>; children?: unknown };
      for (const key of ['placeholder', 'value', 'accessibilityLabel', 'accessibilityHint']) {
        if (typeof props?.[key] === 'string') out.push(props[key] as string);
      }
      walk(children);
    }
  };
  walk(screen.toJSON());
  return out.join('\n');
}

async function fillToReview() {
  await fireEvent.press(await screen.findByTestId('post-category-ERRANDS'));
  await fireEvent.changeText(screen.getByTestId('post-title'), 'Grocery run');
  await fireEvent.press(screen.getByTestId('post-next'));

  await fireEvent.changeText(await screen.findByTestId('post-description'), 'Bread and milk.');
  await fireEvent.press(screen.getByTestId('post-next'));

  await fireEvent.changeText(await screen.findByTestId('post-address'), 'Starčevića 1');
  await fireEvent.press(screen.getByTestId('post-address-search'));
  await fireEvent.press(await screen.findByTestId('post-address-result-0'));
  await fireEvent.press(screen.getByTestId('post-next'));

  await fireEvent.press(await screen.findByTestId('post-when-tomorrow'));
  await fireEvent.press(screen.getByTestId('post-window-MORNING'));
  await fireEvent.press(screen.getByTestId('post-next'));

  await fireEvent.changeText(await screen.findByTestId('post-budget'), '30');
  await fireEvent.press(screen.getByTestId('post-next'));
  await screen.findByTestId('post-review');
}

afterEach(async () => {
  await i18n.changeLanguage('en');
});

it.each(['hr', 'en', 'de', 'fr'] as Locale[])(
  'posts a task end to end in %s and opens it',
  async (locale) => {
    await i18n.changeLanguage(locale);
    const text = catalogues[locale].postTask;
    const { variables, props, plain, secure } = await renderWizard({
      answers: { PostTask: () => POSTED },
    });

    await screen.findByText(text.what.heading);
    await fillToReview();
    expect(screen.getByText(text.review.title)).toBeTruthy();
    expect(screen.getByTestId('post-review-where').props.children).toBe(
      'Ulica Ante Starčevića 1, Dubrovnik',
    );

    await fireEvent.press(screen.getByTestId('post-submit'));

    await waitFor(() => expect(props.onPosted).toHaveBeenCalledWith('task-9'));
    expect(variables.PostTask).toEqual({
      input: {
        category: 'ERRANDS',
        title: 'Grocery run',
        description: 'Bread and milk.',
        location: { address: 'Ulica Ante Starčevića 1, Dubrovnik', lat: 42.6583, lng: 18.0861 },
        flexibleDate: false,
        windowStart: '2026-11-20T07:00:00.000Z',
        windowEnd: '2026-11-20T11:00:00.000Z',
        budgetCents: 3_000,
        photoIds: [],
      },
    });
    // Posting deletes the draft, its address and access details included.
    expect(plain.data.size).toBe(0);
    expect(secure.data.size).toBe(0);
  },
);

it('never suggests a budget: the field starts empty with no amount nearby (hard rule k)', async () => {
  const plain = memory();
  const draft: TaskDraft = {
    ...emptyDraft(),
    step: 'budget',
    category: 'GARDEN',
    title: 'Mow the lawn',
  };
  plain.data.set('taskbeavo.taskDraft', JSON.stringify(draft));
  await renderWizard({ plain });
  await fireEvent.press(await screen.findByTestId('post-resume-continue'));

  const field = await screen.findByTestId('post-budget');
  expect(field.props.value).toBe('');
  expect(field.props.placeholder).toBeUndefined();
  const shown = shownText();
  expect(shown).toContain(catalogues.en.postTask.budget.hint);
  // No amount anywhere: only the step counter has digits.
  expect(shown.replace('Step 5 of 5', '').replace('5/5', '')).not.toMatch(/\d/);

  // Going on without one asks for the poster's own amount, without proposing any.
  await fireEvent.press(screen.getByTestId('post-next'));
  expect(await screen.findByText(catalogues.en.postTask.errors.budget)).toBeTruthy();
  expect(shownText().replace('Step 5 of 5', '').replace('5/5', '')).not.toMatch(/\d/);
});

it('saves the draft as the poster goes', async () => {
  const plain = memory();
  await renderWizard({ plain });
  await fireEvent.press(await screen.findByTestId('post-category-GARDEN'));
  await fireEvent.changeText(screen.getByTestId('post-title'), 'Mow the lawn');
  await fireEvent.press(screen.getByTestId('post-next'));
  await screen.findByTestId('post-description');
  await waitFor(() => expect(plain.data.get('taskbeavo.taskDraft')).toContain('"details"'));
  expect(plain.data.get('taskbeavo.taskDraft')).toContain('Mow the lawn');
});

it('resumes a saved draft where the poster left', async () => {
  const plain = memory();
  const secure = memory();
  await createTaskDraftStore(plain, secure).save({
    ...emptyDraft(),
    step: 'details',
    category: 'GARDEN',
    title: 'Mow the lawn',
  });
  await renderWizard({ plain, secure });

  expect(
    await screen.findByText(catalogues.en.postTask.resume.body.replace('{title}', 'Mow the lawn')),
  ).toBeTruthy();
  await fireEvent.press(screen.getByTestId('post-resume-continue'));
  await screen.findByTestId('post-description');
  await fireEvent.press(screen.getByTestId('screen-back'));
  expect((await screen.findByTestId('post-title')).props.value).toBe('Mow the lawn');
});

it('starts again on request and deletes the old draft', async () => {
  const plain = memory();
  const secure = memory();
  const store = createTaskDraftStore(plain, secure);
  await store.save({
    ...emptyDraft(),
    title: 'Old',
    address: 'Ulica 1',
    lat: 42.6,
    lng: 18.1,
    accessDetails: 'Code 4417',
  });
  await renderWizard({ plain, secure });

  await fireEvent.press(await screen.findByTestId('post-resume-start-again'));

  expect((await screen.findByTestId('post-title')).props.value).toBe('');
  await waitFor(() => expect(secure.data.get('taskbeavo.taskDraftPrivate')).not.toContain('4417'));
});

it('searches addresses only on tap, and keeps the draft with a retry when it fails', async () => {
  const search = fakeSearch(true);
  const plain = memory();
  await renderWizard({ addressSearch: search, plain });
  await fireEvent.press(await screen.findByTestId('post-category-GARDEN'));
  await fireEvent.changeText(screen.getByTestId('post-title'), 'Mow the lawn');
  await fireEvent.press(screen.getByTestId('post-next'));
  await fireEvent.changeText(await screen.findByTestId('post-description'), 'Front lawn.');
  await fireEvent.press(screen.getByTestId('post-next'));

  await fireEvent.changeText(await screen.findByTestId('post-address'), 'Gruž');
  expect(search.search).not.toHaveBeenCalled();
  await fireEvent.press(screen.getByTestId('post-address-search'));

  expect(await screen.findByTestId('post-address-failed')).toBeTruthy();
  expect(search.search).toHaveBeenCalledTimes(1);
  expect(plain.data.get('taskbeavo.taskDraft')).toContain('Mow the lawn');
  // No coordinates, no going on: the wizard never makes them up.
  await fireEvent.press(screen.getByTestId('post-next'));
  expect(await screen.findByText(catalogues.en.postTask.errors.address)).toBeTruthy();
});

it('shows the OpenStreetMap attribution next to the results', async () => {
  const plain = memory();
  plain.data.set(
    'taskbeavo.taskDraft',
    JSON.stringify({ ...emptyDraft(), step: 'where', title: 'Mow the lawn' }),
  );
  await renderWizard({ plain });
  await fireEvent.press(await screen.findByTestId('post-resume-continue'));
  await fireEvent.changeText(await screen.findByTestId('post-address'), 'Gruž');
  await fireEvent.press(screen.getByTestId('post-address-search'));
  expect((await screen.findByTestId('post-address-attribution')).props.children).toBe(
    '© OpenStreetMap contributors',
  );
});

it('adds an on-site contact and keeps only its id in the draft (hard rule e)', async () => {
  const plain = memory();
  const secure = memory();
  plain.data.set(
    'taskbeavo.taskDraft',
    JSON.stringify({ ...emptyDraft(), step: 'where', title: 'Mow the lawn' }),
  );
  const created = { ...CONTACT, id: 'contact-2', name: 'Stric Ivo', phone: '+385987654321' };
  const { variables } = await renderWizard({
    plain,
    secure,
    answers: { CreateCareContact: () => ({ createCareContact: created }) },
  });
  await fireEvent.press(await screen.findByTestId('post-resume-continue'));
  await fireEvent.press(await screen.findByTestId('post-contact-add'));

  await fireEvent.changeText(screen.getByTestId('post-contact-name'), 'Stric Ivo');
  await fireEvent.changeText(screen.getByTestId('post-contact-phone'), '+385 98 765 4321');
  await fireEvent.changeText(screen.getByTestId('post-contact-area'), 'Lapad');
  await fireEvent.press(screen.getByTestId('post-contact-no-smartphone'));
  expect(screen.getByTestId('post-contact-save').props.accessibilityState.disabled).toBe(true);
  await fireEvent.press(screen.getByTestId('post-contact-agreement'));
  await fireEvent.press(screen.getByTestId('post-contact-save'));

  await waitFor(() =>
    expect(screen.getByTestId('post-contact-contact-2').props.accessibilityState.checked).toBe(
      true,
    ),
  );
  expect(variables.CreateCareContact).toEqual({
    input: {
      name: 'Stric Ivo',
      phone: '+385 98 765 4321',
      area: 'Lapad',
      language: 'HR',
      noSmartphone: true,
      hasTheirAgreement: true,
    },
  });
  await waitFor(() => expect(plain.data.get('taskbeavo.taskDraft')).toContain('contact-2'));
  for (const stored of [...plain.data.values(), ...secure.data.values()]) {
    expect(stored).not.toContain('765');
  }
});

it('offers the waitlist when the address is outside the service area', async () => {
  const plain = memory();
  const { variables, props } = await renderWizard({
    plain,
    answers: {
      PostTask: () => ({ error: 'OUT_OF_SERVICE_AREA' }),
      JoinWaitlist: () => ({
        joinWaitlist: {
          __typename: 'WaitlistEntry',
          id: 'wait-1',
          city: 'Dubrovnik',
          countryCode: 'HR',
          category: 'ERRANDS',
        },
      }),
    },
  });
  await fillToReview();
  await fireEvent.press(screen.getByTestId('post-submit'));

  expect(await screen.findByTestId('post-waitlist')).toBeTruthy();
  expect(props.onPosted).not.toHaveBeenCalled();
  expect(plain.data.get('taskbeavo.taskDraft')).toContain('Grocery run');
  await waitFor(() =>
    expect(screen.getByTestId('post-waitlist-email').props.value).toBe('ana@example.com'),
  );
  expect(screen.getByTestId('post-waitlist-join').props.accessibilityState.disabled).toBe(true);
  await fireEvent.press(screen.getByTestId('post-waitlist-consent'));
  await fireEvent.press(screen.getByTestId('post-waitlist-join'));

  expect(await screen.findByTestId('post-waitlist-joined')).toBeTruthy();
  expect(variables.JoinWaitlist).toEqual({
    input: {
      email: 'ana@example.com',
      city: 'Dubrovnik',
      countryCode: 'HR',
      category: 'ERRANDS',
      locale: 'EN',
      consentLaunchEmail: true,
    },
  });
});

it('sends a poster without a verified number to verify it', async () => {
  const { props } = await renderWizard({
    answers: { PostTask: () => ({ error: 'PHONE_NOT_VERIFIED' }) },
  });
  await fillToReview();
  await fireEvent.press(screen.getByTestId('post-submit'));

  expect(await screen.findByText(catalogues.en.errors.PHONE_NOT_VERIFIED)).toBeTruthy();
  await fireEvent.press(screen.getByTestId('post-verify-phone'));
  expect(props.onVerifyPhone).toHaveBeenCalled();
});

it('asks for what is missing before going on', async () => {
  await renderWizard();
  await fireEvent.press(await screen.findByTestId('post-next'));

  expect(screen.getByTestId('post-category-error').props.children).toBe(
    catalogues.en.postTask.errors.category,
  );
  expect(screen.getByText(catalogues.en.postTask.errors.title)).toBeTruthy();
  expect(screen.getByTestId('post-title')).toBeTruthy();
});

it('edits a part from the review and comes back to it', async () => {
  await renderWizard({ answers: { PostTask: () => POSTED } });
  await fillToReview();

  await fireEvent.press(screen.getByTestId('post-edit-budget'));
  await fireEvent.changeText(await screen.findByTestId('post-budget'), '45');
  await fireEvent.press(screen.getByTestId('post-next'));

  expect(await screen.findByTestId('post-review')).toBeTruthy();
  expect(screen.getByTestId('post-review-budget').props.children).toBe('€45.00');
});

it('edits the description and photos from the review, then walks the steps again', async () => {
  const plain = memory();
  const secure = memory();
  await createTaskDraftStore(plain, secure).save({
    ...emptyDraft(),
    step: 'review',
    category: 'ERRANDS',
    title: 'Grocery run',
    description: 'Bread and milk.',
    photos: [
      { id: 'upload-1', url: null },
      { id: 'upload-2', url: null },
    ],
    address: 'Ulica Ante Starčevića 1, Dubrovnik',
    lat: 42.6583,
    lng: 18.0861,
    city: 'Dubrovnik',
    dateMode: 'flexible',
    budget: '30',
  });
  const { variables, props } = await renderWizard({
    plain,
    secure,
    answers: { PostTask: () => POSTED },
  });
  await fireEvent.press(await screen.findByTestId('post-resume-continue'));
  expect((await screen.findByTestId('post-review-details')).props.children).toBe('Bread and milk.');

  // A photo the upload sweep removed, or a typo: both are fixed from the review.
  await fireEvent.press(screen.getByTestId('post-edit-details'));
  await fireEvent.changeText(await screen.findByTestId('post-description'), 'Bread, milk, eggs.');
  await fireEvent.press(screen.getByTestId('post-photo-remove-0'));
  await fireEvent.press(screen.getByTestId('post-next'));
  expect((await screen.findByTestId('post-review-details')).props.children).toBe(
    'Bread, milk, eggs.',
  );
  expect(screen.getByText('1 photo')).toBeTruthy();

  // Back at the review, the edit is over: Back goes to the budget step, not round in a loop.
  await fireEvent.press(screen.getByTestId('screen-back'));
  expect(await screen.findByTestId('post-budget')).toBeTruthy();
  await fireEvent.press(screen.getByTestId('screen-back'));
  expect(await screen.findByTestId('post-when-flexible')).toBeTruthy();
  await fireEvent.press(screen.getByTestId('post-next'));
  await fireEvent.press(await screen.findByTestId('post-next'));
  await fireEvent.press(await screen.findByTestId('post-submit'));

  await waitFor(() => expect(props.onPosted).toHaveBeenCalledWith('task-9'));
  expect(variables.PostTask).toMatchObject({
    input: { description: 'Bread, milk, eggs.', photoIds: ['upload-2'] },
  });
});

it('closes from the first step without losing the draft', async () => {
  const plain = memory();
  const { props } = await renderWizard({ plain });
  await fireEvent.changeText(await screen.findByTestId('post-title'), 'Mow the lawn');
  await fireEvent.press(screen.getByTestId('screen-back'));
  expect(props.onClose).toHaveBeenCalled();
  await waitFor(() => expect(plain.data.get('taskbeavo.taskDraft')).toContain('Mow the lawn'));
});
