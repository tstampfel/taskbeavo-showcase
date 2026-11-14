'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import styles from './pinned-foot.module.css';

/**
 * The most of the screen the order bar may take while pinned. Above it (large text, German or
 * French wording, a short or landscape screen) the bar stays at the end of the page instead,
 * so it never covers the consent boxes or the helper's details (review R1-1, WCAG 2.2 2.4.11).
 */
export const MAX_PINNED_SHARE = 0.35;

/** Whether a bar this tall may be pinned on a screen this tall. */
export function fitsPinned(barHeight: number, viewportHeight: number): boolean {
  return viewportHeight > 0 && barHeight <= viewportHeight * MAX_PINNED_SHARE;
}

/**
 * A step's button bar (checkout's order bar, mockup 6a; the cancel buttons, mockup 13): pinned
 * to the bottom of the screen only while it fits, measured with the user's real text size and
 * language. While pinned, the page's `scroll-padding-bottom` is the bar's height, so a focused
 * box scrolls clear of it. It bleeds into the large side padding of the page's `main`.
 */
export function PinnedFoot({
  children,
  testId,
  className,
}: {
  children: ReactNode;
  testId: string;
  /** Extra layout for this page, e.g. no bottom bleed when something follows the bar. */
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(false);

  useEffect(() => {
    const foot = ref.current;
    if (!foot) return;
    const root = document.documentElement;
    const measure = () => {
      const height = foot.getBoundingClientRect().height;
      const fits = fitsPinned(height, window.innerHeight);
      setPinned(fits);
      root.style.scrollPaddingBottom = fits ? `${Math.ceil(height)}px` : '';
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(foot);
    window.addEventListener('resize', measure);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', measure);
      root.style.scrollPaddingBottom = '';
    };
  }, []);

  return (
    <div
      ref={ref}
      className={className ? `${styles.foot} ${className}` : styles.foot}
      data-pinned={pinned}
      data-testid={testId}
    >
      {children}
    </div>
  );
}
