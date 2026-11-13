import { createNavigation } from 'next-intl/navigation';
import { routing } from './routing';

/** Locale-aware Link and hooks: paths are written without the locale prefix. */
export const { Link, usePathname, useRouter } = createNavigation(routing);
