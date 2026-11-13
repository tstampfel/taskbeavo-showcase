import type { HTMLAttributes } from 'react';
import styles from './card.module.css';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** `raised` has a soft shadow; `flat` an outline, for cards inside cards or sheets. */
  variant?: 'raised' | 'flat';
  selected?: boolean;
}

/** White content card from the mockups' `.card`. */
export function Card({ variant = 'raised', selected = false, className, ...props }: CardProps) {
  const classes = [styles.card, styles[variant], selected && styles.selected, className];
  return <div className={classes.filter(Boolean).join(' ')} {...props} />;
}
