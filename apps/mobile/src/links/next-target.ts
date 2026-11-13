import { isAppRoute } from '@taskbeavo/app-links';
import type { Href } from 'expo-router';

/**
 * Where sign-in or phone verification goes when done: the screen a link wanted (`next`) if it
 * is one a link may open, otherwise home. Nothing else, so `next` can't lead anywhere odd.
 */
export function nextTarget(next: string | string[] | undefined): Href {
  const path = Array.isArray(next) ? next[0] : next;
  return (path && isAppRoute(path) ? path : '/') as Href;
}
