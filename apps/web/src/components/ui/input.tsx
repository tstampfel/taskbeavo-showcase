import type { InputHTMLAttributes } from 'react';
import { useId } from 'react';
import styles from './input.module.css';

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'aria-invalid'> {
  /** Visible label, already translated. Every field has one. */
  label: string;
  hint?: string;
  /** Translated error message; marks the field invalid and is read out with it. */
  error?: string;
}

/** Labelled text field from the mockups' `.input`, with hint and error wired to aria-describedby. */
export function Input({ label, hint, error, id, className, ...props }: InputProps) {
  const fallbackId = useId();
  const inputId = id ?? fallbackId;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [errorId, hintId, props['aria-describedby']].filter(Boolean).join(' ');

  return (
    <div className={[styles.field, className].filter(Boolean).join(' ')}>
      <label htmlFor={inputId} className={styles.label}>
        {label}
      </label>
      <input
        {...props}
        id={inputId}
        className={styles.input}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
      />
      {hint ? (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
