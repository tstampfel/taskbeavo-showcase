import { gql, InMemoryCache } from '@apollo/client';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { typePolicies } from '@taskbeavo/graphql';
import { AsyncStorageWrapper } from 'apollo3-cache-persist';
import { CACHE_KEY, createCachePersistor } from '../src/apollo/cache-persist';

/** Marker values for what must never reach AsyncStorage (NOT_PERSISTED). */
const SECRETS = [
  'MARKER-ADDRESS Ul. od Puča 7',
  '42.640713',
  '18.107719',
  'MARKER-ACCESS gate code 4512, key under the mat',
  'MARKER-PHONE +385 91 234 5678',
  'MARKER-MASKED +385 ••• 678',
  'MARKER-STREET Ul. Branitelja 3',
  'MARKER-POSTAL 20000',
  'MARKER-TOKEN access',
  'MARKER-TOKEN refresh',
  'https://r2.example/MARKER-SIGNED-PHOTO',
  'https://connect.stripe.com/MARKER-ONBOARDING',
  'MARKER-WS-TOKEN',
  'MARKER-TICKET',
];

const TASK = gql`
  query Task($id: ID!) {
    task(id: $id) {
      id
      title
      approximateLat
      address
      lat
      lng
      accessDetails
      careContact {
        id
        name
        area
        phone
      }
    }
  }
`;

const task = {
  task: {
    __typename: 'Task',
    id: 't1',
    title: 'Groceries for Mama',
    approximateLat: 42.64,
    address: 'MARKER-ADDRESS Ul. od Puča 7',
    lat: 42.640713,
    lng: 18.107719,
    accessDetails: 'MARKER-ACCESS gate code 4512, key under the mat',
    careContact: {
      __typename: 'CareContact',
      id: 'c1',
      name: 'Mama Kata',
      area: 'Gruž, Dubrovnik',
      phone: 'MARKER-PHONE +385 91 234 5678',
    },
  },
};

/** The app's own persistor configuration, on the AsyncStorage stand-in. */
function launch() {
  const cache = new InMemoryCache({ typePolicies });
  const persistor = createCachePersistor(cache, new AsyncStorageWrapper(AsyncStorage), {
    trigger: false,
  });
  return { cache, persistor };
}

beforeEach(() => AsyncStorage.clear());

describe('the persisted cache', () => {
  it('keeps exact addresses, access details, phone numbers and credentials off the disk', async () => {
    const { cache, persistor } = launch();
    cache.writeQuery({ query: TASK, variables: { id: 't1' }, data: task });
    cache.writeQuery({
      query: gql`
        query Me {
          viewer {
            id
            firstName
            phoneNumberMasked
            helperProfile {
              id
              addressStreet
              addressPostalCode
              addressCity
            }
          }
          evidence: upload(id: "up1") {
            id
            status
            url
          }
        }
      `,
      data: {
        viewer: {
          __typename: 'User',
          id: 'u1',
          firstName: 'Ana',
          phoneNumberMasked: 'MARKER-MASKED +385 ••• 678',
          helperProfile: {
            __typename: 'HelperProfile',
            id: 'h1',
            addressStreet: 'MARKER-STREET Ul. Branitelja 3',
            addressPostalCode: 'MARKER-POSTAL 20000',
            addressCity: 'Dubrovnik',
          },
        },
        evidence: {
          __typename: 'Upload',
          id: 'up1',
          status: 'READY',
          url: 'https://r2.example/MARKER-SIGNED-PHOTO',
        },
      },
    });
    // Mutation results: a session, a Stripe link, a WebSocket token.
    cache.writeQuery({
      query: gql`
        mutation Results {
          signIn {
            id
            accessToken
            refreshToken
          }
          startPayoutOnboarding {
            id
            url
          }
          webSocketToken
          verifyEmailCode {
            __typename
            ... on AccountDetailsRequired {
              id
              email
              ticket
            }
          }
          signInWithProvider {
            __typename
            ... on ProviderLinkRequired {
              id
              email
              ticket
            }
          }
        }
      `,
      data: {
        signIn: {
          __typename: 'AuthSession',
          id: 's1',
          accessToken: 'MARKER-TOKEN access',
          refreshToken: 'MARKER-TOKEN refresh',
        },
        startPayoutOnboarding: {
          __typename: 'PayoutOnboardingLink',
          id: 'p1',
          url: 'https://connect.stripe.com/MARKER-ONBOARDING',
        },
        webSocketToken: 'MARKER-WS-TOKEN',
        verifyEmailCode: {
          __typename: 'AccountDetailsRequired',
          id: 'd1',
          email: 'new@example.com',
          ticket: 'MARKER-TICKET email',
        },
        signInWithProvider: {
          __typename: 'ProviderLinkRequired',
          id: 'l1',
          email: 'linked@example.com',
          ticket: 'MARKER-TICKET provider',
        },
      },
      id: 'ROOT_MUTATION',
    });

    // Normalized top-level entries, as a real sign-up result is stored in memory.
    expect(cache.extract()['AccountDetailsRequired:d1']).toMatchObject({
      ticket: 'MARKER-TICKET email',
    });
    expect(cache.extract()['ProviderLinkRequired:l1']).toMatchObject({
      ticket: 'MARKER-TICKET provider',
    });

    await persistor.persist();
    const stored = await AsyncStorage.getItem(CACHE_KEY);

    expect(stored).not.toBeNull();
    for (const secret of SECRETS) expect(stored).not.toContain(secret);
    expect(stored).not.toContain('MARKER');
    // Everything else is still saved.
    for (const kept of ['Groceries for Mama', 'Mama Kata', 'Gruž, Dubrovnik', 'Ana', 'Dubrovnik']) {
      expect(stored).toContain(kept);
    }
    // The in-memory cache still has it all, for the screens of this run.
    expect(cache.readQuery({ query: TASK, variables: { id: 't1' } })).toEqual(task);
  });

  it('makes a screen that needs a left-out field refetch after a launch', async () => {
    const first = launch();
    first.cache.writeQuery({ query: TASK, variables: { id: 't1' }, data: task });
    await first.persistor.persist();

    const next = launch();
    await next.persistor.restore();

    // A cache miss: a cache-first query goes to the network for the address.
    expect(next.cache.readQuery({ query: TASK, variables: { id: 't1' } })).toBeNull();
    // A screen with public fields only still opens from the saved cache.
    expect(
      next.cache.readFragment({
        id: 'Task:t1',
        fragment: gql`
          fragment PublicTask on Task {
            title
            approximateLat
          }
        `,
      }),
    ).toEqual({ __typename: 'Task', title: 'Groceries for Mama', approximateLat: 42.64 });
  });
});
