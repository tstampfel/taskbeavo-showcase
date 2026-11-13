# Public release

The project is published as a portfolio showcase (user decision, 2026-11-09). The code is for viewing only: see `NOTICE` (all rights reserved, no licence).

## Where development happens

Development happens here, in the public repository `tstampfel/taskbeavo-showcase`. Everything pushed to it is published the moment it is pushed, an unmerged branch as much as `develop`.

The private repository `tstampfel/taskbeavo`, where development ran until the move on 2026-11-13, is archived and stays private. It is never rewritten or force-pushed, never added as a remote of this repository, and its history is never pushed here. This repository started with no history from it: one commit made from an export (steps 0–5 below, kept as the record of that one-time move).

`develop` here is never rewritten or force-pushed either. Rewriting can't unpublish a pushed value anyway: if a secret is ever pushed, rotate it, then remove it in a new commit.

Two nets check every push:

| Net | Where | What it checks |
| --- | --- | --- |
| Pre-push hook | The owner's machine; not committed, because it reads a private file outside the repository | Every pushed commit: secrets, the private identifiers list (3c), A4 content, Stripe account IDs, internal URLs and local paths, synthetic identifiers, build output, the noreply author |
| CI, `Public tree` job | Every pull request and every push to `develop` (`docs/ci.md`) | gitleaks over the tree (3a) and over the new commits, then `scripts/check_public_tree.sh` (3d, 3e, 3f, 3h) and its tests. The `Backend` job runs 3g (`scripts/check_synthetic_identifiers.py`) |

Only 3c needs private input, so only the hook runs it. A push is never made with `--no-verify` or any other way around the hook: when it blocks a push, fix the commits and push again.

## Checklist of the move (done 2026-11-13)

1. [x] Create the private identifiers list, a private file outside the repository, one value per line (step 0). Never put these values in the repository.
2. [x] Export `develop` into an empty directory by copying files (step 1).
3. [x] Delete the excluded paths (step 2).
4. [x] Every verification in step 3 prints `PASS` on the export, including 3g: no OIB or IBAN that could be real, only the synthetic ones, and no email address outside the reserved example domains.
5. [x] Create the new, empty repository, make one commit with the noreply address, push (step 4).
6. [x] Set branch protection and the security settings (step 5).
7. [x] Only then: make the new repository public.

## The script

It ran from a clone of the private repository. `PUB` is a scratch directory outside the clone.

### 0. Private inputs (once)

Create the private identifiers list, a private file outside every repository, with one real value per line that must never be published. Check 3c fails while the file is missing or empty. The repository only ever holds the placeholders below, never the values:

```text
<the owner's personal email address>
<the owner's Expo account handle>
<the Jira site host, <name>.atlassian.net>
<the Stripe sandbox platform account ID>
<the Stripe sandbox helper account ID>
<the last 10 characters of the platform account ID>
<the last 10 characters of the helper account ID>
<the owner's phone number, in each format it is written>
<the owner's home town and street>
<the owner's OIB>
<anything else never to publish: family names, other addresses, account numbers>
```

The Stripe and account values are in the private repository's history before BEAV-197, and the email in `git log --format='%ae' | sort -u`. The two "last 10 characters" lines catch Stripe object IDs (events, transfers, payment method configurations) that embed the account suffix. Matching is exact and case-sensitive, so add each spelling that could appear; blank lines are ignored.

### 1. Export

```bash
PUB=$(mktemp -d)
git fetch origin
git archive --format=tar origin/develop | tar -x -C "$PUB"
cd "$PUB"
```

`git archive` exports only tracked files, so `.env` files, `node_modules` and build output can't come along. `.gitattributes` marks the A4 folder and `.reviews/` `export-ignore`, so the archive already leaves them out; step 2 makes sure.

### 2. Delete the excluded paths

```bash
rm -rf docs/decisions/A4-private-helper-payer .reviews
```

| Path                                     | Why                                                                                                                                                             |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `docs/decisions/A4-private-helper-payer` | The A4 tax analysis and the binding opinion request draft. Held back until Porezna uprava answers (user decision, 2026-11-09). It is the only place they live. |
| `.reviews/`                              | Review reports from the agent workflow. It is gitignored, but one older report is tracked.                                                                       |

Everything else in `docs/decisions`, the draft legal texts and the open questions stay public.

### 3. Verify the export

Run each block from `$PUB`. Each one must print `PASS`.

```bash
# 3a. Secrets: gitleaks with the repository's config (default rules; the test placeholders are allowlisted)
gitleaks dir . --config .gitleaks.toml --redact --no-banner && echo PASS

# 3b. The excluded paths are gone
test ! -e docs/decisions/A4-private-helper-payer && test ! -e .reviews && echo PASS

# 3c. No real personal value or account ID (fails while the private list is missing or empty)
IDS=<the private identifiers list>
if test -s "$IDS" && grep -q '[^[:space:]]' "$IDS"; then
  grep -rlaF -f <(grep -v '^[[:space:]]*$' "$IDS") .; rc=$?
  case $rc in 1) echo PASS ;; 0) echo "FAIL: the values above are in the export" ;; *) echo "FAIL: grep error $rc" ;; esac
else
  echo "FAIL: $IDS is missing or empty; create it first (step 0)"
fi

# 3d, 3e, 3f, 3h: A4 content, Stripe IDs, internal URLs and local paths, build output
scripts/check_public_tree.sh . && echo PASS

# 3g. Only synthetic OIBs and IBANs, and emails on reserved domains (the same check CI runs)
python3 scripts/check_synthetic_identifiers.py --root . && echo PASS
```

`gitleaks` comes from a release binary (`gh release download -R gitleaks/gitleaks`) or Docker (`zricethezav/gitleaks`); CI pins 8.30.1 and checks the tarball's sha256.

`scripts/check_public_tree.sh [DIR]` holds the patterns of 3d, 3e, 3f and 3h, so CI, these steps and the pre-push hook share one definition. In a git checkout it reads the tracked files; in a plain directory such as `$PUB`, every file. It prints `PASS` or `FAIL` per check and, for a failure, only the names of the files that match, never the values. It exits 1 when a check fails and 2 on an error. `scripts/test_check_public_tree.sh` plants each kind of value in a temp directory and expects a failure.

- **3d** fails on a reference to the A4 folder or its content (the binding opinion request, its questions).
- **3e** fails on a Stripe account (`acct_`) or payment method configuration (`pmc_`) ID outside the allowlist of placeholder IDs the tests use and Stripe's documentation example; a new test placeholder means adding it to the script.
- **3f** fails on internal URLs (the Jira site, Sentry, Honeycomb, Render, Stripe, Vercel and Expo dashboards) and local paths (home directories, agent session folders).
- 3d and 3f skip this file (`docs/public-release.md`), which names the folder and the paths; 3d also skips the root `.gitattributes`, which names the excluded folder. Only those exact paths are skipped: a file with the same name elsewhere is checked. The script checks itself: it writes such patterns with a one-character class (`paye[r]`), which matches the same text but keeps the literal out of its source. 3c and 3f also read binary files (`grep -a`), because a compiled or generated file can embed a local path or a value, and print only the names of the files that match.
- **3h** fails on `__pycache__`, `.pyc`, `.DS_Store`, SQLite, `.db` and `.log` files, and otherwise lists every binary file in the export (images, fonts, compiled translations) so you can check each one is meant to be public.

3g fails on any 11-digit number with a valid OIB check digit that doesn't start with `00000000`, on any IBAN with valid check digits whose account part isn't zeros and a counter, and on any email address (with `@` or the fullwidth `＠`) whose domain RFC 2606 doesn't reserve: only `example.com`, `example.org`, `example.net` and their subdomains, and names under `.test`, `.example`, `.invalid` and `.localhost` pass. The user part of a URL (a Sentry DSN, a database URL) isn't an address, and spelled-out addresses (`marko (at) example (dot) com`) aren't read, so keep those on example domains by hand. Tests, fixtures, the demo seed, docs and mockups take their OIBs and IBANs from `backend/core/synthetic_identifiers.py` and their addresses from the reserved domains, and each exception (the noreply author address in step 4, the SSH user of the remote URL in step 4, the product's own sender address) is listed with its reason in `scripts/synthetic-identifiers-allowlist.txt`.

Run the blocks in bash or zsh (3c uses process substitution). 3c prints `PASS` only when grep ran and found nothing; a missing or blank list, a match or a grep error prints `FAIL`.

**Dry run (2026-11-09, BEAV-197):** steps 1–3 ran on an export of the BEAV-197 branch in a scratch directory, and every block printed `PASS`. 3c was also run three ways: with no identifiers file it printed `FAIL` (missing or empty); with a scratch list of planted fake values it printed `FAIL` and listed each match; with a scratch list of values not in the tree it printed `PASS`.

### 4. Create the public repository and push

Create an empty repository on GitHub first (no README, no licence, no `.gitignore`), **private** until the push is checked.

```bash
cd "$PUB"
git init -b develop
git config user.name "Tomislav Stampfel"
git config user.email "18047150+tstampfel@users.noreply.github.com"
git add -A
git commit -m "TaskBeavo: public showcase"
git log --format='%an <%ae> | %cn <%ce>'   # one line, the noreply address twice
git remote add origin git@github.com:tstampfel/<public-repo>.git
git push -u origin develop
```

Then on GitHub: set `develop` as the default branch, look through the files once, and make the repository public.

**Later updates:** there are no more exports. Changes are made in this repository on a branch and merged into `develop` through a pull request, as described in "Where development happens". Never push the private history to the public repository.

On GitHub, under Settings → Emails, also turn on "Keep my email addresses private" and "Block command line pushes that expose my email".

### 5. Once the repository is public

- **GitHub Actions:** standard GitHub-hosted runners are free and unmetered for public repositories, so `.github/workflows/ci.yml` runs on every pull request without using the account's minutes. The workflow token stays read-only, and pull requests from forks get no secrets (the CI needs none).
- **Branch protection** is available on the free plan for public repositories. Under Settings → Branches (or Rules → Rulesets) for `develop`, as in `docs/ci.md`:
  - Require a pull request before merging.
  - Require status checks to pass, and require branches to be up to date before merging.
  - Required checks, exactly as the jobs are named: `Backend`, `JS`, `GraphQL drift`, `Web e2e`, `Public tree`. A check appears in the picker only after it has run once, so open one pull request first.
  - Block force pushes and deletion of `develop`.
- **Secret scanning and push protection** are free on public repositories: turn both on under Settings → Code security.
- **Issues, Discussions and Wiki:** turn off what isn't used.

## What BEAV-197 checked

### Secret scan

gitleaks 8.21.2, default rules, over every commit reachable from every branch of the private repository (326 non-merge commits; merges carry no new content) and over the working tree. It found 14 values in the history and 13 in the tree, all test placeholders:

| Rule                  | Where                                                                                                                                                     | What                                                                  |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `stripe-access-token` | `backend/core/tests/test_deploy_settings.py`                                                                                                              | Short fake `sk_`/`rk_` keys that test the refusal of live keys        |
| `generic-api-key`     | `backend/core/tests/test_graphql.py`, `test_graphql_limits.py`, `test_deploy_settings.py`                                                                 | The base64 of a "staging only" test string, used as an encryption key |
| `generic-api-key`     | `apps/mobile/__tests__/checkout-screen.test.tsx`, `stripe-lazy.test.tsx`, `apps/web/e2e/checkout.spec.ts`, `backend/payments/tests/test_stripe_client.py` | Fake PaymentIntent client secrets and a fake publishable key          |

There is no real secret in the tree or the history. The placeholders are allowlisted in `.gitleaks.toml`, so a scan now reports no leaks. A further search of the history for long Stripe keys, webhook secrets, real Sentry DSNs, AWS keys, private keys and Resend, Slack or GitHub tokens found none. No `.env`, `.pem`, `.p12` or `.key` file was ever committed.

### Personal data and internal identifiers

Searched the tree for names, emails, phone numbers, OIBs (with check-digit validation), IBANs, addresses, dates of birth, ID numbers, family details, health, vehicle plates, local paths, Stripe account IDs, Sentry DSNs, Render, Vercel, Neon and Honeycomb IDs, and dashboard links, across the code, `.claude/`, `CLAUDE.md`, `docs/`, `design/mockups`, `.github/` and test fixtures.

Changed in the tree:

| Category                                                       | File                                                                                       | Change                                                                                          |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| Stripe sandbox account IDs (platform and a test helper account) | `backend/payments/sandbox/k_on_credited_charge.sh`                                         | Read from `STRIPE_SANDBOX_ACCOUNT` and `STRIPE_SANDBOX_HELPER_ACCOUNT`                          |
| The same IDs, and object IDs that embed their suffix           | `backend/payments/tests/fixtures/transfer.reversed.json`                                   | Placeholders; the tests don't depend on the values                                              |
| Payment method configuration IDs that embed the account suffix | `docs/storage-inventory.md`                                                                | Described in words                                                                              |
| Jira site name containing the owner's name                     | `.claude/skills/taskbeavo-manager/SKILL.md`                                                | "the Atlassian site the Atlassian MCP is connected to"                                          |
| Expo account handle containing the owner's name                | `apps/mobile/app.config.ts`, `apps/mobile/README.md`                                       | "the owner's Expo account"; the EAS project ID stays (EAS needs it, and every app build has it) |
| Links to the A4 record                                         | `CLAUDE.md` rule k, `.claude/skills/compliance-check/SKILL.md`, `docs/decisions/README.md` | "The decision record is kept outside the public repository", true in both repositories         |
| A test address on a street named like the owner                | `backend/config/tests/test_telemetry.py`                                                   | An obviously fictional street                                                                   |

Checked and left as they are:

- **Test identities are fictional:** names, `example.*` and fictional webmail addresses, `+385 91 234 5678`-style phone numbers (sequential placeholders; Croatia, Germany and Austria have no reserved fictional range), North American numbers from the reserved 555-01xx range, and French ones from ARCEP's fictional range 06 39 98.
- **OIBs and IBANs** were published examples at the time. BEAV-203 replaced them with synthetic ones (one of those "examples" was a real public body's account), and check 3g now enforces it.
- **No real Sentry DSN**, and no Render, Vercel, Neon or Honeycomb IDs or dashboard links. `docs/deploy-render.md` names the planned staging host; nothing is deployed.
- **"The founder"** appears only as a role.
- **The owner's name** appears in `NOTICE` and as the author of the public commit (user decision: it is the CV link). The personal email appears nowhere in the export.
