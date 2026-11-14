import { useId, type HTMLAttributes, type ReactNode } from 'react';
import { ChevronIcon } from './icons';
import styles from './list-card.module.css';

/**
 * The mockups' list card (19a Privacy & data, 21d Legal): a white card of rows split by
 * hairlines, as `ListCard` on mobile (BEAV-215). Its children are `ListRow`, `SwitchRow` or
 * `LinkRow`.
 */
export function ListCard({ className, ...props }: HTMLAttributes<HTMLUListElement>) {
  return <ul className={[styles.card, className].filter(Boolean).join(' ')} {...props} />;
}

/** A row's text: a bold title and, under it, a muted caption. */
function RowText({ title, subtitle, subtitleId }: RowTextProps) {
  return (
    <span className={styles.text}>
      <span className={styles.title}>{title}</span>
      {subtitle ? (
        <span id={subtitleId} className={styles.subtitle}>
          {subtitle}
        </span>
      ) : null}
    </span>
  );
}

interface RowTextProps {
  title: string;
  subtitle?: string;
  subtitleId?: string;
}

/** A row that only shows something, e.g. a value at its end. */
export function ListRow({
  title,
  subtitle,
  trailing,
  testId,
}: {
  title: string;
  subtitle?: string;
  trailing?: ReactNode;
  testId?: string;
}) {
  return (
    <li className={styles.row} data-testid={testId}>
      <RowText title={title} subtitle={subtitle} />
      {trailing}
    </li>
  );
}

/**
 * A row with the mockups' `.toggle` at its end. The whole row is the switch's label, so all of
 * it is the tap target; the caption is read out as the switch's description.
 */
export function SwitchRow({
  title,
  subtitle,
  checked,
  disabled,
  onChange,
  testId,
}: {
  title: string;
  subtitle?: string;
  checked: boolean;
  disabled: boolean;
  onChange(value: boolean): void;
  testId: string;
}) {
  const id = useId();
  const subtitleId = subtitle ? `${id}-subtitle` : undefined;
  return (
    <li className={styles.item}>
      <label className={`${styles.row} ${styles.pressable}`} htmlFor={id}>
        <RowText title={title} subtitle={subtitle} subtitleId={subtitleId} />
        <input
          id={id}
          type="checkbox"
          role="switch"
          className={styles.switch}
          checked={checked}
          disabled={disabled}
          aria-describedby={subtitleId}
          onChange={(event) => onChange(event.target.checked)}
          data-testid={testId}
        />
      </label>
    </li>
  );
}

/** A row that opens a page, with a chevron at its end (19a's Privacy notice row). */
export function LinkRow({
  title,
  subtitle,
  href,
  testId,
}: {
  title: string;
  subtitle?: string;
  href: string;
  testId?: string;
}) {
  return (
    <li className={styles.item}>
      <a className={`${styles.row} ${styles.pressable}`} href={href} data-testid={testId}>
        <RowText title={title} subtitle={subtitle} />
        <span className={styles.chevron}>
          <ChevronIcon size={18} />
        </span>
      </a>
    </li>
  );
}
