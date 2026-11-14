import { AccountMenu } from '../account-menu';
import { LanguageSwitcher } from '../language-switcher';
import { PageBanners } from './page-banners';
import styles from './shell.module.css';

/**
 * The header of public pages (home, legal, fees, ranking, sign-in): the language switcher and
 * "Log in", or the account menu once signed in; then the terms and cookie banners. Logged-in
 * pages use AppShell instead.
 */
export function SiteHeader() {
  return (
    <>
      <header className={styles.site} data-testid="site-header">
        <LanguageSwitcher />
        <AccountMenu />
      </header>
      <PageBanners />
    </>
  );
}
