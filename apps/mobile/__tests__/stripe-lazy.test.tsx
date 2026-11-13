import { render, screen } from '@testing-library/react-native';
import type * as CheckoutRouteModule from '../src/payments/checkout-route';
import '../src/i18n';

// Importing @stripe/stripe-react-native starts Stripe's native SDK: on iOS it sends fraud
// signals to m.stripe.com and stores Stripe's `m` cookie at once (QA F4). Expo Router loads
// every route file at launch, so no route may reach the SDK through a static import; only the
// checkout screens load it, lazily (BEAV-190).

const mockLoads: string[] = [];
jest.mock('@stripe/stripe-react-native', () => {
  mockLoads.push('stripe');
  return {
    StripeProvider: ({ children }: { children: unknown }) => children,
    useStripe: () => ({
      initPaymentSheet: jest.fn(),
      presentPaymentSheet: jest.fn(),
      handleURLCallback: jest.fn(),
    }),
  };
});
jest.mock('../src/screens/checkout-screen', () => {
  const { Text } = jest.requireActual('react-native');
  return { CheckoutScreen: () => <Text testID="checkout-screen">checkout</Text> };
});
jest.mock('expo-router', () => ({ useRouter: () => ({ replace: jest.fn() }) }));

// The app has no Node types; these are the few fs and path calls the import walk needs.
declare const __dirname: string;
const { readdirSync, readFileSync, statSync } = jest.requireActual<{
  readdirSync(path: string): string[];
  readFileSync(path: string, encoding: 'utf8'): string;
  statSync(path: string): { isDirectory(): boolean; isFile(): boolean };
}>('fs');
const { dirname, join, relative, resolve } = jest.requireActual<{
  dirname(path: string): string;
  join(...paths: string[]): string;
  relative(from: string, to: string): string;
  resolve(...paths: string[]): string;
}>('path');

const root = resolve(__dirname, '..');
const STRIPE = '@stripe/stripe-react-native';

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return files(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}

function resolveImport(from: string, spec: string): string | null {
  const base = resolve(dirname(from), spec);
  for (const candidate of [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    join(base, 'index.ts'),
    join(base, 'index.tsx'),
  ]) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      // try the next candidate
    }
  }
  return null;
}

/**
 * Static value imports (`import x from`, `export … from`), not `import type`. A `require` inside a
 * function (checkout-route's lazy loader) runs only when called, so it doesn't count; lint forbids
 * a top-level require.
 */
function staticImports(path: string): string[] {
  const text = readFileSync(path, 'utf8');
  const specs: string[] = [];
  const pattern =
    /(?:^|\n)\s*(?:import\s+(?!type\b)(?:[^'";]*?\sfrom\s+)?|export\s+(?!type\b)[^'";]*?\sfrom\s+)['"]([^'"]+)['"]/g;
  for (const match of text.matchAll(pattern)) specs.push(match[1]!);
  return specs;
}

/** Every file a module statically pulls in, and the files that import the Stripe SDK. */
function stripeImporters(entries: string[]): string[] {
  const seen = new Set<string>();
  const importers = new Set<string>();
  const queue = [...entries];
  while (queue.length) {
    const file = queue.pop()!;
    if (seen.has(file)) continue;
    seen.add(file);
    for (const spec of staticImports(file)) {
      if (spec === STRIPE || spec.startsWith(`${STRIPE}/`)) importers.add(relative(root, file));
      else if (spec.startsWith('.')) {
        const next = resolveImport(file, spec);
        if (next) queue.push(next);
      }
    }
  }
  return [...importers];
}

describe('the Stripe SDK loads only on the checkout screens (BEAV-190)', () => {
  it('no route, including the root layout, reaches it through a static import', () => {
    expect(stripeImporters(files(join(root, 'app')))).toEqual([]);
  });

  it('the root layout neither imports nor renders Stripe', () => {
    const layout = readFileSync(join(root, 'app/_layout.tsx'), 'utf8');
    expect(layout).not.toMatch(/stripe|StripeProvider|initStripe|StripeCheckout/i);
  });

  it('only checkout-route loads the SDK module, lazily', () => {
    const importers = files(join(root, 'src')).filter((file) =>
      /require\(\s*['"]\.\/stripe['"]\s*\)|from\s+['"][^'"]*\/?stripe['"]/.test(
        readFileSync(file, 'utf8'),
      ),
    );
    expect(importers.map((file) => relative(root, file))).toEqual([
      'src/payments/checkout-route.tsx',
    ]);
    expect(stripeImporters([join(root, 'src/payments/checkout-route.tsx')])).toEqual([]);
  });

  it('starts the SDK when a checkout screen renders, not when its route loads', async () => {
    process.env.EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY = 'pk_test_abc123';
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const checkoutRoute: typeof CheckoutRouteModule = require('../src/payments/checkout-route');
    const { CheckoutRoute } = checkoutRoute;
    expect(mockLoads).toEqual([]);

    await render(<CheckoutRoute bookingId="booking-1" />);

    expect(await screen.findByTestId('checkout-screen')).toBeTruthy();
    expect(mockLoads).toEqual(['stripe']);
  });
});
