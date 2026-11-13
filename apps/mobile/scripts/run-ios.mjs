// `pnpm --filter @taskbeavo/mobile ios`: builds the development app and runs it on the booted
// iOS simulator (or another device passed with --device).
//
// `expo run:ios` signs even a simulator build when the app has the Sign in with Apple or
// associated-domains entitlement. On a Mac without a code signing certificate it then stops
// with "Your computer requires some additional setup before you can build onto physical iOS
// devices" (BEAV-218). So when the keychain has no signing identity, this builds the
// simulator app unsigned, without those two entitlements (withUnsignedSimulator in app.config.ts).
import { spawnSync } from 'node:child_process';

const UNSIGNED_SIMULATOR_ENV = 'TASKBEAVO_IOS_UNSIGNED_SIMULATOR';

function hasSigningIdentity() {
  const result = spawnSync('security', ['find-identity', '-v', '-p', 'codesigning'], {
    encoding: 'utf8',
  });
  const match = /(\d+) valid identities found/.exec(result.stdout ?? '');
  return match !== null && Number(match[1]) > 0;
}

function expo(args, env) {
  const result = spawnSync('expo', args, { stdio: 'inherit', env });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

const env = { ...process.env };
if (hasSigningIdentity()) {
  delete env[UNSIGNED_SIMULATOR_ENV];
} else {
  env[UNSIGNED_SIMULATOR_ENV] = '1';
  console.log(
    '› No code signing identity in the keychain: building an unsigned simulator app. ' +
      'Sign in with Apple and universal links are off in this build; add an Apple ID in ' +
      'Xcode > Settings > Accounts to sign it.',
  );
}

// `expo run:ios` prebuilds only when ios/ is missing, so sync it with the entitlements chosen
// above every time.
expo(['prebuild', '--platform', 'ios', '--no-install'], env);
expo(['run:ios', ...process.argv.slice(2)], env);
