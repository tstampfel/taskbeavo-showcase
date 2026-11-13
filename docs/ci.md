# CI

`.github/workflows/ci.yml` runs on every pull request to `develop`, on every push to `develop`, and on demand (`workflow_dispatch`). Each step runs a command from the CLAUDE.md Commands table, so a failure reproduces locally with the same command.

| Job (status check) | What it runs                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Backend`          | PostGIS 16 and Redis 7 as services, GDAL from apt, then `uv sync --frozen`, the synthetic identifiers check (`scripts/check_synthetic_identifiers.py`), `ruff check`, `ruff format --check`, `pyright`, `makemigrations --check --dry-run` and `pytest`                                                                                                                                                                                                                                         |
| `JS`               | `pnpm install --frozen-lockfile`, `pnpm format:check`, then `pnpm turbo run lint`, `typecheck` and `test`, then `pnpm --filter @taskbeavo/i18n check:catalogues`: fails on a key missing from (or extra in) hr, de or fr, invalid ICU, or a key no app code uses; warns, without failing, when German or French is much longer than English in a marked layout (see the `i18n-strings` skill)                       |
| `GraphQL drift`    | `pnpm --filter @taskbeavo/graphql check:generated`: fails when `schema.graphql` no longer matches the backend's `export_schema`, or `src/generated/` no longer matches the schema and operations                                                                                                                                                                                                                    |
| `Web e2e`          | Playwright against `pnpm dev` in Chromium, mobile (375px) and desktop projects. The specs mock `/api/graphql` and the subscriptions socket, so no backend runs, and they must pass the same with a local Django on `:8000` (the checkout and offers specs use `e2e/graphql-stub.ts`, which fails a test on any request it has no answer for). On failure the report is uploaded as the `playwright-report` artifact |

## Blocking merges

GitHub only blocks a merge on a check that branch protection requires. A repository admin enables it once, under Settings → Branches (or Rules → Rulesets) for `develop`:

- Require a pull request before merging.
- Require status checks to pass, and require branches to be up to date before merging.
- Required checks: `Backend`, `JS`, `GraphQL drift`, `Web e2e`.

A check appears in that picker only after it has run once, so open the first pull request with this workflow before enabling them. Renaming a job renames its check: update the required checks in the same change.

While the repository is private on GitHub's free plan, GitHub refuses branch protection and rulesets (HTTP 403). The checks still run and show on every pull request, but nothing stops a red merge. Enforce them after upgrading to GitHub Pro (or Team), or after making the repository public. What changes on publication, including Actions minutes, is in `docs/public-release.md`.

## Known gap: GDAL version

The `Backend` job installs GDAL from Ubuntu 24.04's apt (3.8.x), while the `python:3.12-slim` image in `backend/Dockerfile` ships a newer GDAL (3.10.x). Nothing in the tests depends on GDAL behaviour yet. When geometry tests land (the service-area check, hard rule j), either run the job in `container: python:3.12-slim` with the Dockerfile's apt packages, or confirm the results match the image.

## Supply chain

- JS installs use `--frozen-lockfile`, so CI installs exactly what the lockfile pins, and pnpm's `minimumReleaseAge` check applied when each version was locked.
- The backend installs with `uv sync --frozen` from `backend/uv.lock`, with the same uv version as `backend/Dockerfile`. `exclude-newer = "14 days"` in `backend/pyproject.toml` keeps `uv lock` from picking a release less than 14 days old, like pnpm's `minimumReleaseAge`.
- Third-party actions are pinned to a full commit SHA, with the release tag in a comment. When you bump one, choose a release at least two weeks old.
- The workflow token is read-only (`permissions: contents: read`), and checkout does not keep credentials in `.git/config`.
