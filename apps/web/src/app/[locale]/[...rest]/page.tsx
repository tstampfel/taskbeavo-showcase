import { notFound } from 'next/navigation';

/** Sends unknown paths under a locale to the translated [locale]/not-found page. */
export default function CatchAll() {
  notFound();
}
