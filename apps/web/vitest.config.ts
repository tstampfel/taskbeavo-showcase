import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx', '*.test.ts'],
    // next-intl imports `next/server` without an extension; bundling it lets Vite resolve that.
    server: { deps: { inline: ['next-intl'] } },
  },
});
