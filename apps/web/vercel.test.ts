import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const config = JSON.parse(readFileSync(new URL('./vercel.json', import.meta.url), 'utf8')) as {
  framework?: string;
  regions?: string[];
};

describe('vercel.json', () => {
  it('runs functions only in Frankfurt (fra1) for EU data residency', () => {
    expect(config.regions).toEqual(['fra1']);
  });

  it('builds as a Next.js project', () => {
    expect(config.framework).toBe('nextjs');
  });
});
