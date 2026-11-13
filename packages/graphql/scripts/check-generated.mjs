#!/usr/bin/env node
/**
 * Exits non-zero when schema.graphql no longer matches the backend schema, or when
 * src/generated/ no longer matches schema.graphql and the operations. CI runs it as
 * `pnpm --filter @taskbeavo/graphql check:generated`.
 *
 * The schema is printed by the backend's export_schema command, which needs the backend
 * installed (`uv sync`) with GDAL available, but no database. SCHEMA_EXPORT_CMD replaces
 * that command, for example to run it through docker compose on a machine without GDAL:
 *
 *   SCHEMA_EXPORT_CMD="docker compose run --rm -T --no-deps django \
 *     uv run python manage.py export_schema config.schema" pnpm ... check:generated
 *
 * The command runs in BACKEND_DIR (default: the repo's backend/) and must print the SDL.
 */
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const packageDir = process.cwd();
const backendDir = process.env.BACKEND_DIR ?? resolve(scriptDir, '../../../backend');
const exportCmd =
  process.env.SCHEMA_EXPORT_CMD ?? 'uv run python manage.py export_schema config.schema';

function fail(message) {
  console.error(`check:generated: ${message}`);
  process.exit(1);
}

const exported = spawnSync(exportCmd, {
  cwd: backendDir,
  shell: true,
  encoding: 'utf8',
  stdio: ['ignore', 'pipe', 'inherit'],
});
if (exported.status !== 0) fail(`the schema export command failed: ${exportCmd}`);

// export_schema --path writes no trailing newline; printing to stdout adds one.
const committed = readFileSync(join(packageDir, 'schema.graphql'), 'utf8');
if (committed.trimEnd() !== exported.stdout.trimEnd()) {
  fail(
    'packages/graphql/schema.graphql is stale. Run the Schema export command from CLAUDE.md, ' +
      'then `pnpm --filter @taskbeavo/graphql codegen`.',
  );
}

const codegen = spawnSync(
  join(scriptDir, '../node_modules/.bin/graphql-codegen'),
  ['--config', 'codegen.ts', '--check'],
  { cwd: packageDir, stdio: 'inherit' },
);
if (codegen.status !== 0) {
  fail('src/generated/ is stale. Run `pnpm --filter @taskbeavo/graphql codegen`.');
}

console.log('check:generated: schema.graphql and src/generated/ are up to date.');
