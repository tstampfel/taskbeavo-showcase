import type { BadgeTone } from '@taskbeavo/ui-tokens';
import type { HTMLAttributes } from 'react';
import styles from './badge.module.css';

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

/** Status pill from the mockups' `.badge`. Not interactive, so it has no tap target. */
export function Badge({ tone = 'grey', className, ...props }: BadgeProps) {
  return (
    <span
      className={[styles.badge, styles[tone], className].filter(Boolean).join(' ')}
      {...props}
    />
  );
}
