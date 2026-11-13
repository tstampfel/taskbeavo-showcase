/// <reference types="node" />
import { spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import pkg from '../package.json';

const script = path.join(__dirname, '..', 'scripts', 'run-ios.mjs');

/**
 * Runs scripts/run-ios.mjs with fake `security` and `expo` commands on PATH. Each fake
 * `expo` call appends its arguments and the unsigned-simulator variable to a log.
 */
function runIos(identities: string, args: string[] = []) {
  const bin = mkdtempSync(path.join(tmpdir(), 'run-ios-'));
  const log = path.join(bin, 'expo.log');
  writeFileSync(
    path.join(bin, 'security'),
    `#!/bin/sh\nprintf '%s\\n' ${JSON.stringify(identities)}\n`,
  );
  writeFileSync(
    path.join(bin, 'expo'),
    `#!/bin/sh\necho "$* | unsigned=$TASKBEAVO_IOS_UNSIGNED_SIMULATOR" >> ${JSON.stringify(log)}\n`,
  );
  chmodSync(path.join(bin, 'security'), 0o755);
  chmodSync(path.join(bin, 'expo'), 0o755);
  const env = { ...process.env, PATH: `${bin}:${process.env.PATH}` };
  Reflect.deleteProperty(env, 'TASKBEAVO_IOS_UNSIGNED_SIMULATOR');
  const result = spawnSync(process.execPath, [script, ...args], { env, encoding: 'utf8' });
  const calls = readFileSync(log, 'utf8').trim().split('\n');
  return { result, calls };
}

const none = '     0 valid identities found';
const one =
  '  1) 0123456789ABCDEF0123456789ABCDEF01234567 "Apple Development: Example (EXAMPLE123)"\n     1 valid identities found';

describe('scripts/run-ios.mjs', () => {
  it('is the mobile ios command', () => {
    expect(pkg.scripts.ios).toBe('node scripts/run-ios.mjs');
  });

  it('builds an unsigned simulator app when the keychain has no signing identity', () => {
    const { result, calls } = runIos(none);
    expect(result.status).toBe(0);
    expect(calls).toEqual([
      'prebuild --platform ios --no-install | unsigned=1',
      'run:ios | unsigned=1',
    ]);
    expect(result.stdout).toMatch(/Sign in with Apple and universal links are off/);
  });

  it('keeps the signed build when a signing identity exists', () => {
    const { result, calls } = runIos(one);
    expect(result.status).toBe(0);
    expect(calls).toEqual([
      'prebuild --platform ios --no-install | unsigned=',
      'run:ios | unsigned=',
    ]);
  });

  it('passes extra arguments through to expo run:ios', () => {
    const { calls } = runIos(none, ['--device', 'iPhone 17 Pro', '--no-bundler']);
    expect(calls.at(-1)).toBe('run:ios --device iPhone 17 Pro --no-bundler | unsigned=1');
  });
});
