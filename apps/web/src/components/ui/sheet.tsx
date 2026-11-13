'use client';

import type { ReactNode } from 'react';
import { useEffect, useId, useRef } from 'react';
import styles from './sheet.module.css';

export interface SheetProps {
  open: boolean;
  onClose: () => void;
  /** Heading, already translated; it names the dialog. */
  title: string;
  /** Accessible name of the close button, already translated. */
  closeLabel: string;
  children: ReactNode;
}

/**
 * Bottom sheet from the mockups' `.sheet`, on a native modal <dialog>: the browser traps focus,
 * makes the page inert, and closes it on Escape.
 */
export function Sheet({ open, onClose, title, closeLabel, children }: SheetProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={styles.sheet}
      aria-labelledby={titleId}
      onCancel={(event) => {
        // Let the parent own `open`, so Escape and the close button behave the same.
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        // The content fills the dialog, so a click on the dialog itself is on the backdrop.
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className={styles.content}>
        <div className={styles.grab} aria-hidden="true" />
        <div className={styles.header}>
          <h2 id={titleId} className={styles.title}>
            {title}
          </h2>
          <button type="button" className={styles.close} aria-label={closeLabel} onClick={onClose}>
            <span aria-hidden="true">×</span>
          </button>
        </div>
        <div className={styles.body}>{children}</div>
      </div>
    </dialog>
  );
}
