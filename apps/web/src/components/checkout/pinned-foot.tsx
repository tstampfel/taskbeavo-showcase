'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import styles from './checkout.module.css';

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
 * The checkout's order bar (mockup 6a): pinned to the bottom of the screen only while it fits,
 * measured with the user's real text size and language. While pinned, the page's
 * `scroll-padding-bottom` is the bar's height, so a focused box scrolls clear of it.
 */
export function PinnedFoot({ children }: { children: ReactNode }) {
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
    <div ref={ref} className={styles.foot} data-pinned={pinned} data-testid="checkout-foot">
      {children}
    </div>
  );
}
