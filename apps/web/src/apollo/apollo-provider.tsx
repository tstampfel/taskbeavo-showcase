'use client';

import { ApolloNextAppProvider } from '@apollo/client-integration-nextjs';
import type { ReactNode } from 'react';
import { makeClient } from './client';

/** Apollo for client components (logged-in pages). */
export function ApolloProvider({ children }: { children: ReactNode }) {
  return <ApolloNextAppProvider makeClient={makeClient}>{children}</ApolloNextAppProvider>;
}
