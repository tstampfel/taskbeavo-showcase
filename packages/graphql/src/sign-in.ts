import type {
  AccountDetailsInput,
  Locale as ApiLocale,
  SignInProvider,
  ViewerFieldsFragment,
} from './generated/graphql';

/**
 * The sign-in flow both apps share (mockup 15-signup-login): what comes after each API
 * answer, and the checks the forms run before calling the API. The screens own the UI and
 * the transport (web: the /api/auth route handlers, mobile: Apollo and SecureStore).
 */

/** Sign-in codes, emailed or by SMS, have six digits. */
export const CODE_LENGTH = 6;

/** The email-code resend timer; the API doesn't say, and its limit is 5 an hour. */
export const EMAIL_RESEND_SECONDS = 60;

/** What a sign-in mutation answered, as both apps see it (web gets no tokens). */
export type SignInAnswer =
  | { __typename: 'AuthSession'; id: string; user: ViewerFieldsFragment }
  | {
      __typename: 'AccountDetailsRequired';
      id: string;
      email: string;
      ticket: string;
      /** Only signInWithProvider shares a name. */
      firstName?: string | null;
      lastName?: string | null;
    }
  | { __typename: 'ProviderLinkRequired'; id: string; email: string; ticket: string };

export type SignInStep =
  | { kind: 'signedIn'; user: ViewerFieldsFragment }
  | {
      kind: 'details';
      email: string;
      ticket: string;
      firstName: string;
      lastName: string;
      /** Set when the account comes from Google or Apple (mockup 15e2). */
      provider: SignInProvider | undefined;
    }
  /**
   * The provider's email already has an account. It is never linked automatically: the
   * user proves the address with the code the API just emailed to it.
   */
  | { kind: 'linkCode'; email: string; ticket: string; provider: SignInProvider };

export function stepAfterSignIn(answer: SignInAnswer, provider?: SignInProvider): SignInStep {
  switch (answer.__typename) {
    case 'AuthSession':
      return { kind: 'signedIn', user: answer.user };
    case 'AccountDetailsRequired':
      return {
        kind: 'details',
        email: answer.email,
        ticket: answer.ticket,
        firstName: answer.firstName ?? '',
        lastName: answer.lastName ?? '',
        provider,
      };
    case 'ProviderLinkRequired':
      // The API only answers this to signInWithProvider, so a provider is always known.
      return {
        kind: 'linkCode',
        email: answer.email,
        ticket: answer.ticket,
        provider: provider ?? 'GOOGLE',
      };
  }
}

/** Posting, offering and chat need a verified mobile number, so sign-in asks for it next. */
export function needsPhoneVerification(user: Pick<ViewerFieldsFragment, 'phoneVerified'>): boolean {
  return !user.phoneVerified;
}

/** The digits of a typed or pasted code, at most six. */
export function normalizeCode(input: string): string {
  return input.replace(/\D/g, '').slice(0, CODE_LENGTH);
}

/** Seconds as "0:42" or "29:12", rounded up so it never shows 0:00 while still waiting. */
export function formatCountdown(seconds: number): string {
  const total = Math.max(0, Math.ceil(seconds));
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(total % 60).padStart(2, '0')}`;
}

/** Catches obvious typos before a round trip; the API decides (INVALID_EMAIL). */
export function isPlausibleEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
}

/** The account-details form (mockups 15e and 15e2). */
export interface AccountDetailsForm {
  firstName: string;
  lastName: string;
  /** ISO 3166-1 alpha-2. */
  country: string;
  locale: Lowercase<ApiLocale>;
  isAdult: boolean;
  /** The required, unticked terms and privacy box. */
  acceptedTerms: boolean;
  /**
   * The TERMS version in force (CurrentTerms), which the box accepts; undefined while it
   * loads or when none could be loaded. The API rejects any other with TERMS_NOT_ACCEPTED.
   */
  termsVersion: string | undefined;
}

/** "Create account" stays disabled until this is true. */
export function canCreateAccount(form: AccountDetailsForm): boolean {
  return (
    form.firstName.trim() !== '' &&
    form.lastName.trim() !== '' &&
    /^[A-Z]{2}$/.test(form.country) &&
    form.isAdult &&
    form.acceptedTerms &&
    form.termsVersion !== undefined
  );
}

/**
 * The API refused the terms version: they changed while the form was open. The form
 * fetches the new version and unticks the box, so the person accepts what they now see.
 */
export function isTermsNotAccepted(error: unknown): boolean {
  return signInErrorOf(error).code === 'TERMS_NOT_ACCEPTED';
}

export function accountDetailsInput(
  ticket: string,
  form: AccountDetailsForm,
  timeZone: string,
): AccountDetailsInput {
  return {
    ticket,
    firstName: form.firstName.trim(),
    lastName: form.lastName.trim(),
    isAdult: form.isAdult,
    locale: form.locale.toUpperCase() as ApiLocale,
    country: form.country,
    timeZone,
    // Only a ticked box accepts anything.
    acceptedTermsVersion: form.acceptedTerms ? (form.termsVersion ?? null) : null,
  };
}

// --- Errors ------------------------------------------------------------------------------

interface GraphQLErrorEntry {
  message?: string;
  extensions?: { code?: unknown; retryAfterSeconds?: unknown } | null;
}

/** An API error the web app's /api/auth client throws: the code is translated as `errors.<CODE>`. */
export class SignInError extends Error {
  constructor(
    readonly code: string | undefined,
    readonly retryAfterSeconds?: number,
  ) {
    super(`Sign-in failed: ${code ?? 'network'}`);
    this.name = 'SignInError';
  }

  /** From a response's `errors`; no code (e.g. none sent) reads as a network failure. */
  static fromErrors(errors: readonly GraphQLErrorEntry[] | undefined): SignInError {
    return new SignInError(...codeAndRetry(errors?.[0]));
  }
}

function codeAndRetry(entry: GraphQLErrorEntry | undefined): [string | undefined, number?] {
  const code = entry?.extensions?.code;
  const retry = entry?.extensions?.retryAfterSeconds;
  return [
    typeof code === 'string' ? code : undefined,
    typeof retry === 'number' && retry > 0 ? retry : undefined,
  ];
}

export interface SignInErrorInfo {
  /** The API error code; undefined for a network failure or an unexpected answer. */
  code: string | undefined;
  /** For RATE_LIMITED: how long to wait. */
  retryAfterSeconds: number | undefined;
}

/** The code and retry time of an Apollo error (mobile) or a SignInError (web). */
export function signInErrorOf(error: unknown): SignInErrorInfo {
  if (error instanceof SignInError) {
    return { code: error.code, retryAfterSeconds: error.retryAfterSeconds };
  }
  // Apollo Client 4's CombinedGraphQLErrors, checked by name as in telemetry.ts.
  const candidate = error as { name?: unknown; errors?: unknown } | null | undefined;
  if (candidate?.name === 'CombinedGraphQLErrors' && Array.isArray(candidate.errors)) {
    const [code, retryAfterSeconds] = codeAndRetry((candidate.errors as GraphQLErrorEntry[])[0]);
    return { code, retryAfterSeconds };
  }
  return { code: undefined, retryAfterSeconds: undefined };
}
