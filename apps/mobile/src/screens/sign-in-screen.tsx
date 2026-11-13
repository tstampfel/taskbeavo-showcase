import {
  CODE_LENGTH,
  EMAIL_RESEND_SECONDS,
  accountDetailsInput,
  canCreateAccount,
  formatCountdown,
  isPlausibleEmail,
  isTermsNotAccepted,
  signInErrorOf,
  stepAfterSignIn,
  type AccountDetailsForm,
  type SignInAnswer,
  type SignInProvider,
  type SignInStep,
  type ViewerFieldsFragment,
} from '@taskbeavo/graphql';
import { defaultCountry, languageNames, locales, toApiLocale, type Locale } from '@taskbeavo/i18n';
import { colors, controlHeight, minTapTarget, radii, spacing } from '@taskbeavo/ui-tokens';
import { IntlMessageFormat } from 'intl-messageformat';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AccessibilityInfo,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { ProviderCredential, SignInApi } from '../auth/sign-in-api';
import { Checkbox } from '../components/sign-in/checkbox';
import { CodeField } from '../components/sign-in/code-field';
import { CountryPicker } from '../components/sign-in/country-picker';
import { errorMessage } from '../components/sign-in/error-message';
import { useSecondsLeft } from '../components/sign-in/use-seconds-left';
import { Button, Input } from '../components/ui';
import { textStyle } from '../components/ui/text-style';
import { fonts } from '../fonts';
import {
  answerLocalePrompt,
  chooseLocale,
  localeAfterSignIn,
  type LanguageDeps,
} from '../i18n/preference';

/** A provider's native sign-in, given our nonce; null when the user cancelled. */
export type ProviderSignIn = (nonce: string) => Promise<ProviderCredential | null>;

export interface SignInScreenProps {
  api: Pick<
    SignInApi,
    | 'requestEmailCode'
    | 'verifyEmailCode'
    | 'requestNonce'
    | 'signInWithProvider'
    | 'linkProvider'
    | 'completeAccountDetails'
    | 'currentTermsVersion'
  >;
  /** Only the providers available on this phone. */
  providers: { apple?: ProviderSignIn; google?: ProviderSignIn };
  language: LanguageDeps;
  timeZone: string;
  deviceLanguageTags: readonly string[];
  /** Opens the terms or privacy page in the in-app browser. */
  openLegal(page: 'terms' | 'privacy', locale: Locale): void;
  /** Called once signed in and the language is settled. */
  onSignedIn(user: ViewerFieldsFragment): void;
}

type Screen =
  | { kind: 'welcome' }
  | { kind: 'email' }
  | { kind: 'emailCode'; email: string }
  | Exclude<SignInStep, { kind: 'signedIn' }>
  | { kind: 'localePrompt'; user: ViewerFieldsFragment; picked: Locale; account: Locale };

/**
 * Sign-in and sign-up (mockups 15a to 15e2): Apple (iOS), Google or an email code, then the
 * account-details step for a new account. The API's tokens go straight into SecureStore
 * (`createSignInApi`); this screen only sees the user.
 */
export function SignInScreen(props: SignInScreenProps) {
  const { t, i18n } = useTranslation();
  const { api, language } = props;
  const [screen, setScreen] = useState<Screen>({ kind: 'welcome' });
  const [resendAt, setResendAt] = useState<number | null>(null);
  const locale = i18n.language as Locale;

  async function sendEmailCode(email: string) {
    await api.requestEmailCode(email, toApiLocale(locale));
    setResendAt(Date.now() + EMAIL_RESEND_SECONDS * 1000);
  }

  async function signedIn(user: ViewerFieldsFragment) {
    // The account's language takes over unless another one was picked on this phone.
    const decision = await localeAfterSignIn(user.locale, language);
    if (decision.kind === 'askToSave') {
      setScreen({ kind: 'localePrompt', user, picked: decision.picked, account: decision.account });
    } else {
      props.onSignedIn(user);
    }
  }

  async function handle(answer: SignInAnswer, provider?: SignInProvider) {
    const step = stepAfterSignIn(answer, provider);
    if (step.kind === 'signedIn') await signedIn(step.user);
    else setScreen(step);
  }

  switch (screen.kind) {
    case 'welcome':
      return (
        <Welcome
          providers={props.providers}
          onEmail={() => setScreen({ kind: 'email' })}
          onProvider={async (provider, signIn) => {
            const nonce = await api.requestNonce();
            const credential = await signIn(nonce);
            if (!credential) return;
            await handle(await api.signInWithProvider(provider, nonce, credential), provider);
          }}
          onLanguage={language}
        />
      );
    case 'email':
      return (
        <EmailStep
          onBack={() => setScreen({ kind: 'welcome' })}
          onSubmit={async (email) => {
            await sendEmailCode(email);
            setScreen({ kind: 'emailCode', email });
          }}
        />
      );
    case 'emailCode':
      return (
        <CodeStep
          testID="email-code"
          header={t('signIn.codeHeader')}
          title={t('signIn.codeTitle')}
          intro={t('signIn.codeSentTo', { email: screen.email })}
          changeLabel={t('signIn.wrongEmail')}
          onChange={() => setScreen({ kind: 'email' })}
          resendAt={resendAt}
          onResend={() => sendEmailCode(screen.email)}
          onSubmit={async (code) => handle(await api.verifyEmailCode(screen.email, code))}
        />
      );
    case 'linkCode':
      return (
        <CodeStep
          testID="link-code"
          header={t('signIn.linkHeader')}
          title={t('signIn.codeTitle')}
          intro={t('signIn.linkBody', { email: screen.email, provider: screen.provider })}
          changeLabel={t('signIn.back')}
          onChange={() => setScreen({ kind: 'welcome' })}
          onSubmit={async (code) => handle(await api.linkProvider(screen.ticket, code))}
        />
      );
    case 'details':
      return (
        <DetailsStep
          email={screen.email}
          provider={screen.provider}
          initial={{
            firstName: screen.firstName,
            lastName: screen.lastName,
            country: defaultCountry(props.deviceLanguageTags),
          }}
          openLegal={props.openLegal}
          currentTermsVersion={api.currentTermsVersion}
          onBack={() => setScreen({ kind: 'welcome' })}
          onSubmit={async (form) =>
            handle(
              await api.completeAccountDetails(
                accountDetailsInput(screen.ticket, form, props.timeZone),
              ),
            )
          }
        />
      );
    case 'localePrompt':
      return (
        <LocalePrompt
          picked={screen.picked}
          account={screen.account}
          onAnswer={async (yes) => {
            await answerLocalePrompt(yes, screen.picked, language);
            props.onSignedIn(screen.user);
          }}
        />
      );
  }
}

// --- Steps -------------------------------------------------------------------------------

export function Page({
  title,
  onBack,
  children,
  footer,
  testID,
}: {
  title?: string;
  onBack?: () => void;
  children: ReactNode;
  footer?: ReactNode;
  testID: string;
}) {
  const { t } = useTranslation();
  return (
    <SafeAreaView style={styles.safeArea} testID={testID}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {onBack || title ? (
          <View style={styles.top}>
            {onBack ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={t('signIn.back')}
                onPress={onBack}
                style={styles.iconButton}
                testID={`${testID}-back`}
              >
                <Text
                  style={styles.backArrow}
                  importantForAccessibility="no"
                  accessibilityElementsHidden
                >
                  ‹
                </Text>
              </Pressable>
            ) : (
              <View style={styles.iconButton} />
            )}
            <Text
              style={styles.topTitle}
              accessibilityRole="header"
              maxFontSizeMultiplier={TITLE_MAX_FONT_SCALE}
            >
              {title}
            </Text>
            <View style={styles.iconButton} />
          </View>
        ) : null}
        <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
          {children}
        </ScrollView>
        {footer ? <View style={styles.foot}>{footer}</View> : null}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function useAction() {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<void>): Promise<unknown> {
    setBusy(true);
    setError(null);
    try {
      await action();
      return undefined;
    } catch (caught) {
      setError(errorMessage(t, caught));
      return caught;
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, setError, run };
}

export function ErrorText({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <Text accessibilityRole="alert" accessibilityLiveRegion="polite" style={styles.error}>
      {error}
    </Text>
  );
}

function Welcome({
  providers,
  onEmail,
  onProvider,
  onLanguage,
}: {
  providers: SignInScreenProps['providers'];
  onEmail(): void;
  onProvider(provider: SignInProvider, signIn: ProviderSignIn): Promise<void>;
  onLanguage: LanguageDeps;
}) {
  const { t, i18n } = useTranslation();
  const { busy, error, run } = useAction();
  const { apple, google } = providers;
  return (
    <Page
      testID="sign-in-welcome"
      footer={
        <>
          <ErrorText error={error} />
          {apple ? (
            <AppleButton
              label={t('signIn.continueWithApple')}
              busy={busy}
              onPress={() => void run(() => onProvider('APPLE', apple))}
            />
          ) : null}
          {google ? (
            <Button
              label={t('signIn.continueWithGoogle')}
              variant="secondary"
              busy={busy}
              onPress={() => void run(() => onProvider('GOOGLE', google))}
              testID="google-sign-in"
            />
          ) : null}
          <Button
            label={t('signIn.continueWithEmail')}
            variant="secondary"
            disabled={busy}
            onPress={onEmail}
            testID="email-sign-in"
          />
        </>
      }
    >
      <View style={styles.welcome}>
        <Text accessibilityRole="header" style={styles.display}>
          {t('signIn.welcomeTitle')}
        </Text>
        <Text style={styles.mutedCenter}>{t('signIn.welcomeBody')}</Text>
        <LanguageRow current={i18n.language as Locale} deps={onLanguage} />
      </View>
    </Page>
  );
}

/**
 * Apple's guidelines want its button black (or white) with the logo, not in our brand green;
 * black is the one colour here that isn't a token. The label stays in the app's language.
 */
const APPLE_BLACK = '#000000';

function AppleButton({ label, busy, onPress }: { label: string; busy: boolean; onPress(): void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ busy, disabled: busy }}
      disabled={busy}
      onPress={onPress}
      style={[styles.apple, busy && styles.appleBusy]}
      testID="apple-sign-in"
    >
      <Text style={styles.appleLabel}>{`\uF8FF ${label}`}</Text>
    </Pressable>
  );
}

function LanguageRow({ current, deps }: { current: Locale; deps: LanguageDeps }) {
  const { t } = useTranslation();
  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel={t('languageSwitcher.label')}
      style={styles.segments}
    >
      {locales.map((option) => {
        const selected = option === current;
        return (
          <Pressable
            key={option}
            accessibilityRole="radio"
            accessibilityLabel={languageNames[option]}
            accessibilityLanguage={option}
            accessibilityState={{ selected, checked: selected }}
            onPress={() => {
              // An explicit pick: sign-in asks before replacing it with the account's.
              if (!selected) void chooseLocale(option, deps);
            }}
            style={[styles.segment, selected && styles.segmentOn]}
            testID={`language-${option}`}
          >
            <Text style={[styles.segmentLabel, selected && styles.segmentLabelOn]}>
              {option.toUpperCase()}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function EmailStep({
  onBack,
  onSubmit,
}: {
  onBack(): void;
  onSubmit(email: string): Promise<void>;
}) {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const { busy, error, setError, run } = useAction();
  const submit = () => {
    const address = email.trim();
    if (!isPlausibleEmail(address)) {
      setError(t('signIn.emailInvalid'));
      return;
    }
    void run(() => onSubmit(address));
  };
  return (
    <Page
      testID="sign-in-email"
      title={t('signIn.continueWithEmail')}
      onBack={onBack}
      footer={
        <Button label={t('signIn.sendCode')} busy={busy} onPress={submit} testID="send-code" />
      }
    >
      <Text accessibilityRole="header" style={styles.h1}>
        {t('signIn.emailTitle')}
      </Text>
      <Input
        label={t('signIn.emailLabel')}
        placeholder={t('signIn.emailPlaceholder')}
        hint={t('signIn.emailHint')}
        error={error ?? undefined}
        keyboardType="email-address"
        autoCapitalize="none"
        autoComplete="email"
        textContentType="emailAddress"
        autoCorrect={false}
        autoFocus
        value={email}
        onChangeText={setEmail}
        onSubmitEditing={submit}
        testID="email"
      />
    </Page>
  );
}

export function CodeStep({
  header,
  title,
  intro,
  changeLabel,
  onChange,
  onSubmit,
  onResend,
  resendAt = null,
  labels,
  testID,
}: {
  /** The phone step's own wording; the email steps use the signIn defaults. */
  labels?: { verify: string; resend: string; resendIn(time: string): string };
  header: string;
  title: string;
  intro: string;
  changeLabel: string;
  onChange(): void;
  onSubmit(code: string): Promise<void>;
  onResend?: () => Promise<void>;
  resendAt?: number | null;
  testID: string;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  // A second tap while a code is on its way would send (and, for SMS, pay for) another one.
  // The ref stops a tap that lands before the re-render; the state greys the link out.
  const resendingRef = useRef(false);
  const [resending, setResending] = useState(false);
  const lockedSeconds = useSecondsLeft(lockedUntil);
  const resendSeconds = useSecondsLeft(resendAt);
  const words = labels ?? {
    verify: t('signIn.verifyCode'),
    resend: t('signIn.resend'),
    resendIn: (time: string) => t('signIn.resendIn', { time }),
  };

  async function submit(value: string) {
    if (value.length !== CODE_LENGTH || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await onSubmit(value);
    } catch (caught) {
      const { code: errorCode, retryAfterSeconds } = signInErrorOf(caught);
      if (
        (errorCode === 'RATE_LIMITED' || errorCode === 'LOCKED') &&
        retryAfterSeconds !== undefined
      ) {
        setLockedUntil(Date.now() + retryAfterSeconds * 1000);
      } else {
        const message = errorMessage(t, caught);
        setError(message);
        AccessibilityInfo.announceForAccessibility(message);
      }
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page testID={testID} title={header} onBack={onChange}>
      <Text accessibilityRole="header" style={styles.h1}>
        {title}
      </Text>
      <Text style={styles.muted}>{intro}</Text>
      <Pressable
        accessibilityRole="link"
        onPress={onChange}
        style={styles.linkButton}
        testID={`${testID}-change`}
      >
        <Text style={styles.link}>{changeLabel}</Text>
      </Pressable>
      {lockedSeconds > 0 ? (
        <View style={styles.card} accessibilityRole="alert" testID={`${testID}-locked`}>
          <Text style={styles.h2}>{t('signIn.lockedTitle')}</Text>
          <Text style={styles.mutedCenter}>
            {t('signIn.lockedBody', { time: formatCountdown(lockedSeconds) })}
          </Text>
        </View>
      ) : (
        <>
          <CodeField
            label={t('signIn.codeLabel')}
            value={code}
            error={error}
            editable={!busy}
            onChange={(value) => {
              setCode(value);
              if (value.length === CODE_LENGTH) void submit(value);
            }}
            testID={`${testID}-input`}
          />
          {notice ? <Text style={styles.muted}>{notice}</Text> : null}
          <Button
            label={words.verify}
            busy={busy}
            disabled={code.length !== CODE_LENGTH}
            onPress={() => void submit(code)}
            testID={`${testID}-verify`}
          />
          {onResend ? (
            resendSeconds > 0 ? (
              <Text style={styles.muted}>{words.resendIn(formatCountdown(resendSeconds))}</Text>
            ) : (
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ busy: resending, disabled: resending }}
                disabled={resending}
                onPress={() => {
                  if (resendingRef.current) return;
                  resendingRef.current = true;
                  setResending(true);
                  setError(null);
                  onResend()
                    .then(
                      () => setNotice(t('signIn.codeResent')),
                      (caught: unknown) => setError(errorMessage(t, caught)),
                    )
                    .finally(() => {
                      resendingRef.current = false;
                      setResending(false);
                    });
                }}
                style={styles.linkButton}
                testID={`${testID}-resend`}
              >
                <Text style={styles.link}>{words.resend}</Text>
              </Pressable>
            )
          ) : null}
        </>
      )}
    </Page>
  );
}

/** "I agree to the Terms and conditions and have read the Privacy notice", with both linked. */
/**
 * `documentLocale` is the language the account is being created in: the terms open in it, as
 * that is the language the API records the acceptance in.
 */
function TermsLabel({
  openLegal,
  documentLocale,
}: {
  openLegal: SignInScreenProps['openLegal'];
  documentLocale: Locale;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language as Locale;
  const message = String(i18n.getResource(locale, 'translation', 'signIn.terms') ?? '');
  const link = (page: 'terms' | 'privacy') => (chunks: ReactNode[]) => (
    <Text
      key={page}
      accessibilityRole="link"
      onPress={() => openLegal(page, documentLocale)}
      style={styles.inlineLink}
      testID={`legal-${page}`}
    >
      {chunks}
    </Text>
  );
  const parts = new IntlMessageFormat(message, locale).format<ReactNode>({
    terms: link('terms'),
    privacy: link('privacy'),
  });
  return (
    <Text style={styles.small}>
      {parts}
      <Text style={styles.required} accessibilityLabel={t('signIn.required')}>
        {' *'}
      </Text>
    </Text>
  );
}

function DetailsStep({
  email,
  provider,
  initial,
  openLegal,
  currentTermsVersion,
  onBack,
  onSubmit,
}: {
  email: string;
  provider: SignInProvider | undefined;
  initial: Pick<AccountDetailsForm, 'firstName' | 'lastName' | 'country'>;
  openLegal: SignInScreenProps['openLegal'];
  currentTermsVersion: SignInScreenProps['api']['currentTermsVersion'];
  onBack(): void;
  onSubmit(form: AccountDetailsForm): Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const [form, setForm] = useState<Omit<AccountDetailsForm, 'termsVersion'>>({
    ...initial,
    locale: i18n.language as Locale,
    isAdult: false,
    acceptedTerms: false,
  });
  // The box accepts the version in force (the API refuses any other, TERMS_NOT_ACCEPTED).
  // Every language has the same version; none loaded keeps "Create account" disabled.
  const [terms, setTerms] = useState<{ version: string | undefined; loaded: boolean }>({
    version: undefined,
    loaded: false,
  });
  const loadTerms = useCallback(
    (locale: Locale) =>
      currentTermsVersion(toApiLocale(locale)).then(
        (version) => setTerms({ version: version ?? undefined, loaded: true }),
        () => setTerms({ version: undefined, loaded: true }),
      ),
    [currentTermsVersion],
  );
  useEffect(() => {
    void loadTerms(form.locale);
  }, [loadTerms, form.locale]);
  const { busy, error, run } = useAction();
  const update = (change: Partial<Omit<AccountDetailsForm, 'termsVersion'>>) =>
    setForm((current) => ({ ...current, ...change }));
  const withTerms: AccountDetailsForm = { ...form, termsVersion: terms.version };
  const ready = canCreateAccount(withTerms);
  const termsUnavailable = terms.loaded && terms.version === undefined;
  const submit = async () => {
    const caught = await run(() => onSubmit(withTerms));
    if (isTermsNotAccepted(caught)) {
      // The terms changed while the form was open: accept the new version, not the old.
      update({ acceptedTerms: false });
      void loadTerms(form.locale);
    }
  };
  const termsText = t('signIn.terms').replace(/<\/?(terms|privacy)>/g, '');

  return (
    <Page
      testID="account-details"
      title={provider ? t('signIn.providerDetailsTitle') : t('signIn.detailsTitle')}
      onBack={onBack}
      footer={
        <>
          <ErrorText error={error} />
          <Button
            label={t('signIn.createAccount')}
            disabled={!ready}
            busy={busy}
            onPress={() => void submit()}
            testID="create-account"
          />
          {termsUnavailable ? (
            <>
              <Text style={styles.mutedCenter} accessibilityLiveRegion="polite">
                {t('signIn.termsUnavailable')}
              </Text>
              <Button
                label={t('signIn.termsRetry')}
                variant="secondary"
                onPress={() => {
                  setTerms({ version: undefined, loaded: false });
                  void loadTerms(form.locale);
                }}
                testID="terms-retry"
              />
            </>
          ) : !form.acceptedTerms ? (
            <Text style={styles.mutedCenter}>{t('signIn.termsRequired')}</Text>
          ) : null}
        </>
      }
    >
      {provider ? (
        <View style={styles.providerCard}>
          <Text style={styles.strong}>{t('signIn.signedInWith', { provider })}</Text>
          <Text style={styles.muted}>{email}</Text>
        </View>
      ) : null}
      <Input
        label={t('signIn.firstName')}
        autoComplete="given-name"
        textContentType="givenName"
        value={form.firstName}
        onChangeText={(firstName) => update({ firstName })}
        testID="first-name"
      />
      <Input
        label={t('signIn.lastName')}
        autoComplete="family-name"
        textContentType="familyName"
        value={form.lastName}
        onChangeText={(lastName) => update({ lastName })}
        testID="last-name"
      />
      <CountryPicker
        label={t('signIn.country')}
        value={form.country}
        onChange={(country) => update({ country })}
        testID="country"
      />
      <Text style={styles.label} importantForAccessibility="no">
        {t('signIn.appLanguage')}
      </Text>
      <View
        accessibilityRole="radiogroup"
        accessibilityLabel={t('signIn.appLanguage')}
        style={styles.segments}
      >
        {locales.map((option) => {
          const selected = option === form.locale;
          return (
            <Pressable
              key={option}
              accessibilityRole="radio"
              accessibilityLabel={languageNames[option]}
              accessibilityState={{ selected, checked: selected }}
              // The box accepted the terms in the old language: tick it again for this one.
              onPress={() => update({ locale: option, acceptedTerms: false })}
              style={[styles.segment, selected && styles.segmentOn]}
              testID={`account-language-${option}`}
            >
              <Text style={[styles.segmentLabel, selected && styles.segmentLabelOn]}>
                {option.toUpperCase()}
              </Text>
            </Pressable>
          );
        })}
      </View>
      <Checkbox
        checked={form.isAdult}
        onChange={(isAdult) => update({ isAdult })}
        label={t('signIn.isAdult')}
        testID="is-adult"
      />
      <Checkbox
        checked={form.acceptedTerms}
        onChange={(acceptedTerms) => update({ acceptedTerms })}
        label={<TermsLabel openLegal={openLegal} documentLocale={form.locale} />}
        accessibilityLabel={`${termsText}, ${t('signIn.required')}`}
        testID="accept-terms"
      />
    </Page>
  );
}

function LocalePrompt({
  picked,
  account,
  onAnswer,
}: {
  picked: Locale;
  account: Locale;
  onAnswer(yes: boolean): Promise<void>;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const answer = (yes: boolean) => {
    setBusy(true);
    void onAnswer(yes);
  };
  return (
    <Page
      testID="locale-prompt"
      footer={
        <>
          <Button
            label={t('signIn.localePromptYes')}
            busy={busy}
            onPress={() => answer(true)}
            testID="locale-yes"
          />
          <Button
            label={t('signIn.localePromptNo')}
            variant="secondary"
            disabled={busy}
            onPress={() => answer(false)}
            testID="locale-no"
          />
        </>
      }
    >
      <Text accessibilityRole="header" style={styles.h1}>
        {t('signIn.localePromptTitle', { language: languageNames[picked] })}
      </Text>
      <Text style={styles.muted}>
        {t('signIn.localePromptBody', { account: languageNames[account] })}
      </Text>
    </Page>
  );
}

/**
 * The largest text scale for page titles and section headings: beyond it long German and French
 * words break mid-word in the narrow header (BEAV-190 QA F9). Body text keeps the full scale.
 */
export const TITLE_MAX_FONT_SCALE = 1.6;

export const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: colors.offWhite },
  flex: { flex: 1 },
  top: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.sm,
  },
  iconButton: {
    minWidth: minTapTarget,
    minHeight: minTapTarget,
    alignItems: 'center',
    justifyContent: 'center',
  },
  backArrow: { fontFamily: fonts.bold, fontSize: 32, lineHeight: 36, color: colors.green },
  topTitle: { ...textStyle('label'), flex: 1, textAlign: 'center', color: colors.ink },
  body: { flexGrow: 1, gap: spacing.lg, padding: spacing.lg },
  foot: { gap: spacing.md, padding: spacing.lg },
  welcome: { flex: 1, justifyContent: 'center', alignItems: 'center', gap: spacing.lg },
  display: { ...textStyle('display'), color: colors.ink, textAlign: 'center' },
  h1: { ...textStyle('h1'), color: colors.ink },
  h2: { ...textStyle('h2'), color: colors.ink, textAlign: 'center' },
  label: { ...textStyle('label'), color: colors.ink },
  small: { ...textStyle('small'), color: colors.ink },
  strong: { ...textStyle('small'), fontFamily: fonts.bold, color: colors.ink },
  muted: { ...textStyle('small'), color: colors.inkMuted },
  mutedCenter: { ...textStyle('small'), color: colors.inkMuted, textAlign: 'center' },
  error: { ...textStyle('small'), fontFamily: fonts.bold, color: colors.error },
  required: { fontFamily: fonts.bold, color: colors.error },
  link: { ...textStyle('small'), fontFamily: fonts.bold, color: colors.green },
  inlineLink: { fontFamily: fonts.bold, color: colors.green, textDecorationLine: 'underline' },
  linkButton: { alignSelf: 'flex-start', minHeight: minTapTarget, justifyContent: 'center' },
  card: {
    alignItems: 'center',
    gap: spacing.sm,
    padding: spacing.xl,
    borderRadius: radii.lg,
    backgroundColor: colors.white,
  },
  providerCard: {
    gap: spacing.xs,
    padding: spacing.md,
    borderRadius: radii.md,
    backgroundColor: colors.greyTint,
  },
  apple: {
    minHeight: controlHeight.md,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
    borderRadius: radii.pill,
    backgroundColor: APPLE_BLACK,
  },
  appleBusy: { opacity: 0.6 },
  appleLabel: { ...textStyle('body'), fontFamily: fonts.bold, color: colors.white },
  segments: {
    flexDirection: 'row',
    alignSelf: 'stretch',
    gap: 2,
    padding: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.greyTint,
  },
  segment: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: minTapTarget,
    minWidth: minTapTarget,
    borderRadius: radii.pill,
  },
  segmentOn: { backgroundColor: colors.white },
  segmentLabel: { ...textStyle('small'), fontFamily: fonts.bold, color: colors.greyTintText },
  segmentLabelOn: { color: colors.green },
});
