import { spawnSync } from 'node:child_process';
import { appendFileSync, cpSync, mkdtempSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';

const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const script = join(packageDir, 'scripts/check-generated.mjs');
const tempDirs = [];

/** Runs the check from `cwd`, with the "backend" printing `sdlCmd` instead of exporting. */
function check(cwd, sdlCmd) {
  return spawnSync(process.execPath, [script], {
    cwd,
    env: { ...process.env, SCHEMA_EXPORT_CMD: sdlCmd },
    encoding: 'utf8',
  });
}

/** A copy of the package's inputs and outputs, so a test can make one of them stale. */
function packageCopy() {
  // Inside the package, so codegen still resolves the package's node_modules.
  const dir = mkdtempSync(join(packageDir, '.check-generated-test-'));
  tempDirs.push(dir);
  for (const entry of ['schema.graphql', 'codegen.ts', 'src']) {
    cpSync(join(packageDir, entry), join(dir, entry), { recursive: true });
  }
  return dir;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe('check:generated', () => {
  const committedSchema = `cat '${join(packageDir, 'schema.graphql')}'`;

  it('passes when the schema and generated types are current', () => {
    const result = check(packageDir, committedSchema);

    expect(result.stderr).toBe('');
    expect(result.status).toBe(0);
  });

  it('fails when the backend schema has changed', () => {
    const result = check(packageDir, `${committedSchema}; echo 'type Extra { id: ID! }'`);

    expect(result.status).toBe(1);
    expect(result.stderr).toContain('schema.graphql is stale');
  });

  it('fails when the schema export command fails', () => {
    const result = check(packageDir, 'exit 3');

    expect(result.status).toBe(1);
    expect(result.stderr).toContain('schema export command failed');
  });

  it('fails when src/generated/ no longer matches the schema and operations', () => {
    const dir = packageCopy();
    appendFileSync(join(dir, 'src/generated/graphql.ts'), '\n// edited by hand\n');

    const result = check(dir, `cat '${join(dir, 'schema.graphql')}'`);

    expect(result.status).toBe(1);
    expect(result.stderr).toContain('src/generated/ is stale');
  });
});
