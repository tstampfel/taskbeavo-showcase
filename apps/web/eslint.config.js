import nextPlugin from '@next/eslint-plugin-next';
import base from '@taskbeavo/eslint-config';

/**
 * The only modules allowed to load Stripe.js. Stripe.js sets cookies and fraud signals, which are
 * strictly necessary only on the checkout and payment pages; anywhere else they would need a
 * consent banner (BEAV-190). stripe-imports.test.ts checks the same list.
 */
export const STRIPE_JS_MODULES = ['src/components/checkout/booking-checkout.tsx'];

const STRIPE_MESSAGE =
  'Stripe.js loads only on the checkout and payment pages (cookie-exempt storage, BEAV-190). Add the module to STRIPE_JS_MODULES only for a new payment page.';

export default [
  ...base,
  { ignores: ['next-env.d.ts', 'playwright-report/**', 'test-results/**'] },
  {
    plugins: { '@next/next': nextPlugin },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs['core-web-vitals'].rules,
    },
  },
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: STRIPE_JS_MODULES,
    rules: {
      '@typescript-eslint/no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@stripe/stripe-js', '@stripe/stripe-js/*', '@stripe/react-stripe-js'],
              allowTypeImports: true,
              message: STRIPE_MESSAGE,
            },
          ],
        },
      ],
      // no-restricted-imports misses import() and re-exports (review R1-7).
      'no-restricted-syntax': [
        'error',
        { selector: 'ImportExpression[source.value=/^@stripe\\u002F/]', message: STRIPE_MESSAGE },
        {
          selector: 'ExportNamedDeclaration[exportKind!="type"][source.value=/^@stripe\\u002F/]',
          message: STRIPE_MESSAGE,
        },
        {
          selector: 'ExportAllDeclaration[source.value=/^@stripe\\u002F/]',
          message: STRIPE_MESSAGE,
        },
      ],
    },
  },
];
