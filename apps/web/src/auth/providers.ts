import type { Locale } from '@taskbeavo/i18n';

/**
 * Google and Apple sign-in in the browser, through each provider's own script: Google
 * Identity Services renders its button and hands back an ID token; Sign in with Apple JS
 * opens Apple's popup. Each ID token carries the one-time nonce from `requestSignInNonce`,
 * which the API checks and burns. A provider without its public client ID isn't offered.
 */

export interface WebProviderConfig {
  googleClientId?: string;
  appleServicesId?: string;
}

export function webProviderConfig(
  env: Record<string, string | undefined> = {
    // Spelled out so Next.js inlines them into the browser bundle.
    NEXT_PUBLIC_GOOGLE_WEB_CLIENT_ID: process.env.NEXT_PUBLIC_GOOGLE_WEB_CLIENT_ID,
    NEXT_PUBLIC_APPLE_SERVICES_ID: process.env.NEXT_PUBLIC_APPLE_SERVICES_ID,
  },
): WebProviderConfig {
  return {
    googleClientId: env.NEXT_PUBLIC_GOOGLE_WEB_CLIENT_ID || undefined,
    appleServicesId: env.NEXT_PUBLIC_APPLE_SERVICES_ID || undefined,
  };
}

export const GOOGLE_SCRIPT = 'https://accounts.google.com/gsi/client';

const APPLE_SCRIPT_LOCALES: Record<Locale, string> = {
  hr: 'hr_HR',
  en: 'en_US',
  de: 'de_DE',
  fr: 'fr_FR',
};

export function appleScript(locale: Locale): string {
  return `https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/${APPLE_SCRIPT_LOCALES[locale]}/appleid.auth.js`;
}

const scripts = new Map<string, Promise<void>>();

/** Loads a provider script once per page. */
export function loadScript(src: string): Promise<void> {
  let loading = scripts.get(src);
  if (!loading) {
    loading = new Promise<void>((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => {
        scripts.delete(src);
        reject(new Error(`Couldn't load ${src}`));
      };
      document.head.appendChild(script);
    });
    scripts.set(src, loading);
  }
  return loading;
}

interface GoogleIdentity {
  accounts: {
    id: {
      initialize(options: {
        client_id: string;
        nonce: string;
        callback(response: { credential?: string }): void;
        ux_mode?: 'popup';
        context?: 'signin';
        itp_support?: boolean;
      }): void;
      renderButton(
        parent: HTMLElement,
        options: {
          type: 'standard';
          theme: 'outline';
          size: 'large';
          text: 'continue_with';
          shape: 'pill';
          width?: number;
          locale?: string;
        },
      ): void;
    };
  };
}

interface AppleId {
  auth: {
    init(options: {
      clientId: string;
      scope: string;
      redirectURI: string;
      usePopup: boolean;
      nonce: string;
    }): void;
    signIn(): Promise<{
      authorization: { id_token: string };
      user?: { name?: { firstName?: string; lastName?: string } };
    }>;
  };
}

declare global {
  interface Window {
    google?: GoogleIdentity;
    AppleID?: AppleId;
  }
}

/** Renders Google's "Continue with Google" button; `onIdToken` gets each sign-in's ID token. */
export async function renderGoogleButton(
  parent: HTMLElement,
  options: { clientId: string; nonce: string; locale: Locale; onIdToken(idToken: string): void },
): Promise<void> {
  await loadScript(GOOGLE_SCRIPT);
  const google = window.google;
  if (!google) throw new Error('Google Identity Services did not load');
  google.accounts.id.initialize({
    client_id: options.clientId,
    nonce: options.nonce,
    ux_mode: 'popup',
    context: 'signin',
    itp_support: true,
    callback: ({ credential }) => {
      if (credential) options.onIdToken(credential);
    },
  });
  parent.replaceChildren();
  google.accounts.id.renderButton(parent, {
    type: 'standard',
    theme: 'outline',
    size: 'large',
    text: 'continue_with',
    shape: 'pill',
    width: Math.min(400, parent.clientWidth || 400),
    locale: options.locale,
  });
}

export interface AppleSignIn {
  idToken: string;
  firstName: string;
  lastName: string;
}

/** Apple's sign-in popup. Null when the user closed it. */
export async function signInWithApple(options: {
  servicesId: string;
  nonce: string;
  locale: Locale;
  redirectUri: string;
}): Promise<AppleSignIn | null> {
  await loadScript(appleScript(options.locale));
  const apple = window.AppleID;
  if (!apple) throw new Error('Sign in with Apple JS did not load');
  apple.auth.init({
    clientId: options.servicesId,
    scope: 'name email',
    redirectURI: options.redirectUri,
    usePopup: true,
    nonce: options.nonce,
  });
  try {
    const result = await apple.auth.signIn();
    return {
      idToken: result.authorization.id_token,
      firstName: result.user?.name?.firstName ?? '',
      lastName: result.user?.name?.lastName ?? '',
    };
  } catch (error) {
    const reason = (error as { error?: unknown } | null)?.error;
    if (reason === 'popup_closed_by_user' || reason === 'user_cancelled_authorize') return null;
    throw error;
  }
}
