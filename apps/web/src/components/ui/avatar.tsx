import styles from './avatar.module.css';
import { PersonIcon } from './icons';

/**
 * The mockups' round `.av`. Helper names and photos aren't in the API yet (BEAV-144), so it
 * shows a generic person: decorative, the badge next to it says who the helper is.
 */
export function Avatar({ size = 'md' }: { size?: 'sm' | 'md' }) {
  return (
    <span
      className={`${styles.avatar} ${styles[size]}`}
      aria-hidden="true"
      data-testid="helper-avatar"
    >
      <PersonIcon size={size === 'md' ? 24 : 20} />
    </span>
  );
}
