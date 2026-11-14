import type { ReactNode } from 'react';

/**
 * The mockups' line icons (24px grid, 2px stroke, `currentColor`). Always decorative: the text
 * next to each icon says what it means, so they are hidden from screen readers.
 */
function Icon({ size = 16, children }: { size?: number; children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

export function PersonIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" />
    </Icon>
  );
}

export function CalendarIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </Icon>
  );
}

export function InfoIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </Icon>
  );
}

export function LockIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </Icon>
  );
}

export function ClockIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 6v6l4 2" />
    </Icon>
  );
}

export function BellIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </Icon>
  );
}

export function AlertIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="m21.7 18-8-14a2 2 0 0 0-3.4 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3z" />
      <path d="M12 9v4M12 17h.01" />
    </Icon>
  );
}

export function DoorIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M13 4h3a2 2 0 0 1 2 2v14M2 20h3M13 20h9M10 12v.01M13 4.6v16.8a.6.6 0 0 1-.8.6L5 20V5.6a1 1 0 0 1 .8-1L12.2 3.4a.6.6 0 0 1 .8.6z" />
    </Icon>
  );
}

export function PhoneIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z" />
    </Icon>
  );
}

export function NavIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="m3 11 19-9-9 19-2-8z" />
    </Icon>
  );
}

export function ChevronIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M9 5l7 7-7 7" />
    </Icon>
  );
}

export function CheckIcon({ size }: { size?: number }) {
  return (
    <Icon size={size}>
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </Icon>
  );
}
