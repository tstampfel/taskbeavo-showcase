import type { ButtonVariant } from '@taskbeavo/ui-tokens';
import type { ButtonHTMLAttributes } from 'react';
import styles from './button.module.css';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** `sm` is still a 44px tap target. */
  size?: 'md' | 'sm';
  fullWidth?: boolean;
}

/** Pill button from the mockups' `.btn`. The label comes from the caller, already translated. */
export function Button({
  variant = 'primary',
  size = 'md',
  fullWidth = false,
  type = 'button',
  className,
  ...props
}: ButtonProps) {
  const classes = [
    styles.button,
    styles[variant],
    size === 'sm' && styles.sm,
    fullWidth && styles.fullWidth,
    className,
  ];
  return <button type={type} className={classes.filter(Boolean).join(' ')} {...props} />;
}
