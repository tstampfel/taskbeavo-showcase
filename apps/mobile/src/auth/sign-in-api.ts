import type { ApolloClient } from '@apollo/client';
import {
  CompleteAccountDetailsDocument,
  CurrentTermsDocument,
  LinkProviderWithEmailCodeDocument,
  RequestEmailCodeDocument,
  RequestSignInNonceDocument,
  SignInWithProviderDocument,
  VerifyEmailCodeDocument,
  type AccountDetailsInput,
  type AuthSessionFieldsFragment,
  type DeviceInput,
  type Locale as ApiLocale,
  type SignInAnswer,
  type SignInProvider,
} from '@taskbeavo/graphql';
import { storedSession, type SessionManager } from './session';

/** The part of Apollo Client the sign-in calls use. */
export type MutationClient = Pick<ApolloClient, 'mutate'>;

/**
 * Sign-in also resets the cache, which still holds the signed-out answers; `query` reads the
 * terms version the account-details step accepts.
 */
export type SignInClient = Pick<ApolloClient, 'mutate' | 'query' | 'resetStore'>;

export interface ProviderCredential {
  idToken: string;
  /** Apple shares the name only on the first sign-in. */
  firstName?: string;
  lastName?: string;
}

/**
 * The device token the API hands out at sign-in (AuthSession.deviceToken), kept apart from
 * the session because it outlives sign-out: sent back on the next sign-in, it tells the API
 * this phone isn't a new device, so no new-device email goes out. In the app it is
 * SecureStore (token-store.ts).
 */
export interface DeviceTokenStore {
  load(): Promise<string | null>;
  save(token: string): Promise<void>;
}

function required<T>(value: T | null | undefined): T {
  if (value === null || value === undefined) throw new Error('The API answered without data');
  return value;
}

/**
 * The sign-in calls of the app. A session the API returns goes straight into SecureStore
 * through `sessions` and never anywhere else; the screens get the answer with the user. Then
 * the cache is reset, so every mounted screen (home under the sign-in screen, too) refetches
 * as the signed-in user instead of keeping `viewer: null`.
 */
export function createSignInApi(
  client: SignInClient,
  sessions: Pick<SessionManager, 'signedIn'>,
  baseDevice: DeviceInput,
  deviceTokens?: DeviceTokenStore,
) {
  async function device(): Promise<DeviceInput> {
    const deviceToken = await deviceTokens?.load().catch(() => null);
    return deviceToken ? { ...baseDevice, deviceToken } : baseDevice;
  }

  async function keep<T extends { __typename?: string }>(answer: T): Promise<T> {
    if (answer.__typename === 'AuthSession') {
      const session = answer as unknown as AuthSessionFieldsFragment;
      if (session.deviceToken) await deviceTokens?.save(session.deviceToken).catch(() => undefined);
      await sessions.signedIn(storedSession(session));
      // Rejects only when a refetch fails (offline); the cache is cleared either way.
      await client.resetStore().catch(() => undefined);
    }
    return answer;
  }

  return {
    async requestEmailCode(email: string, locale: ApiLocale): Promise<void> {
      await client.mutate({ mutation: RequestEmailCodeDocument, variables: { email, locale } });
    },
    async verifyEmailCode(email: string, code: string): Promise<SignInAnswer> {
      const { data } = await client.mutate({
        mutation: VerifyEmailCodeDocument,
        variables: { email, code, device: await device() },
      });
      return keep(required(data?.verifyEmailCode)) as Promise<SignInAnswer>;
    },
    async requestNonce(): Promise<string> {
      const { data } = await client.mutate({ mutation: RequestSignInNonceDocument });
      return required(data?.requestSignInNonce).nonce;
    },
    async signInWithProvider(
      provider: SignInProvider,
      nonce: string,
      credential: ProviderCredential,
    ): Promise<SignInAnswer> {
      const { data } = await client.mutate({
        mutation: SignInWithProviderDocument,
        variables: {
          input: {
            provider,
            nonce,
            idToken: credential.idToken,
            firstName: credential.firstName ?? '',
            lastName: credential.lastName ?? '',
          },
          device: await device(),
        },
      });
      return keep(required(data?.signInWithProvider)) as Promise<SignInAnswer>;
    },
    async linkProvider(ticket: string, code: string): Promise<SignInAnswer> {
      const { data } = await client.mutate({
        mutation: LinkProviderWithEmailCodeDocument,
        variables: { ticket, code, device: await device() },
      });
      return keep({
        ...required(data?.linkProviderWithEmailCode),
        __typename: 'AuthSession' as const,
      });
    },
    /** The TERMS version in force, which the details step's box accepts; null if none is. */
    async currentTermsVersion(language: ApiLocale): Promise<string | null> {
      const { data } = await client.query({
        query: CurrentTermsDocument,
        variables: { language },
        // Never an old copy: the API refuses any version but the one in force.
        fetchPolicy: 'network-only',
      });
      return data?.currentLegalDocument?.version ?? null;
    },
    async completeAccountDetails(details: AccountDetailsInput): Promise<SignInAnswer> {
      const { data } = await client.mutate({
        mutation: CompleteAccountDetailsDocument,
        variables: { details, device: await device() },
      });
      return keep({
        ...required(data?.completeAccountDetails),
        __typename: 'AuthSession' as const,
      });
    },
  };
}

export type SignInApi = ReturnType<typeof createSignInApi>;
