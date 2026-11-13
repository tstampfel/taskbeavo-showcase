import { SetLocaleDocument } from '@taskbeavo/graphql';
import { apolloClient, sessionManager } from '../apollo/client';
import { i18n } from './index';
import { securePickedLocaleStore, secureLocaleStore } from './locale-store';
import type { LanguageDeps } from './preference';

/** The app's language wiring: i18next, the phone's store and the signed-in account. */
export const appLanguage: LanguageDeps = {
  i18n,
  store: secureLocaleStore,
  picked: securePickedLocaleStore,
  async saveToAccount(locale) {
    if ((await sessionManager.accessToken()) === null) return;
    await apolloClient.mutate({ mutation: SetLocaleDocument, variables: { locale } });
  },
};
