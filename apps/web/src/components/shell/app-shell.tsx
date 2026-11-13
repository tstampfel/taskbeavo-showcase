'use client';

import { useTranslations } from 'next-intl';
import type { ComponentProps, ReactNode } from 'react';
import { Link } from '../../i18n/navigation';
import { AccountMenu } from '../account-menu';
import { PageBanners } from './page-banners';
import styles from './shell.module.css';

/** A page inside the app, as the locale-aware Link takes it: `/bookings/7` or `{ pathname, query }`. */
export type AppHref = ComponentProps<typeof Link>['href'];

export interface TopBarProps {
  /** The page title, already translated. */
  title: string;
  /** The parent page the back button opens; leave it out on a page without a parent. */
  back?: AppHref;
  /** Extra 44px buttons for this page, shown before the account menu. */
  actions?: ReactNode;
  /**
   * The title is the page's `h1` (the default). Set false while the page body still renders
   * its own `h1`, so the page keeps exactly one.
   */
  titleIsHeading?: boolean;
}

export interface AppShellProps extends TopBarProps {
  children: ReactNode;
}

/**
 * The frame of every logged-in page (mockups 04, 06, 13): a top bar with back, the title and
 * the account menu (which holds the language switcher), then the terms and cookie banners,
 * then the page. Public pages use SiteHeader instead.
 */
export function AppShell({ children, ...bar }: AppShellProps) {
  return (
    <>
      <TopBar {...bar} />
      <PageBanners />
      {children}
    </>
  );
}

export function TopBar({ title, back, actions, titleIsHeading = true }: TopBarProps) {
  const t = useTranslations('appShell');
  const Title = titleIsHeading ? 'h1' : 'p';
  return (
    <header className={styles.bar} data-testid="app-top-bar">
      <div className={styles.barInner}>
        <div className={styles.side}>
          {back === undefined ? null : (
            <Link
              href={back}
              className={styles.iconButton}
              aria-label={t('back')}
              data-testid="app-back"
            >
              <BackIcon />
            </Link>
          )}
        </div>
        <Title className={styles.title} data-testid="app-title">
          {title}
        </Title>
        <div className={`${styles.side} ${styles.sideEnd}`}>
          {actions}
          <AccountMenu withLanguage />
        </div>
      </div>
    </header>
  );
}

function BackIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M15 5l-7 7 7 7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
