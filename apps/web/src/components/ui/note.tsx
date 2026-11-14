import type { HTMLAttributes, ReactNode } from 'react';
import styles from './note.module.css';

export interface NoteProps extends HTMLAttributes<HTMLDivElement> {
  /** `info` is green on a green tint, `warn` dark orange on an orange tint (both AA). */
  tone?: 'info' | 'warn';
  /** A decorative icon from ./icons; the text says what the note means. */
  icon: ReactNode;
}

/** The mockups' `.note`: a tinted box with an icon and a short line of text. */
export function Note({ tone = 'info', icon, className, children, ...props }: NoteProps) {
  return (
    <div className={[styles.note, styles[tone], className].filter(Boolean).join(' ')} {...props}>
      <span className={styles.icon}>{icon}</span>
      <span>{children}</span>
    </div>
  );
}
