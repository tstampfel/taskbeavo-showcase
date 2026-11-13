import { InMemoryCache } from '@apollo/client';
import { print } from 'graphql';
import { describe, expect, it } from 'vitest';
import { ApiVersionDocument, typePolicies } from './index';

describe('@taskbeavo/graphql', () => {
  it('exports type policies InMemoryCache accepts', () => {
    expect(() => new InMemoryCache({ typePolicies })).not.toThrow();
  });

  it('exports typed documents generated from src/operations', () => {
    expect(print(ApiVersionDocument)).toContain('query ApiVersion');
  });
});
