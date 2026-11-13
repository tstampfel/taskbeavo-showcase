#!/usr/bin/env bash
# Tests for scripts/check_public_tree.sh and the gitleaks scan CI runs next to it.
# Each case plants one value in a fresh temp directory and expects the check to fail;
# a clean directory must pass. The planted values are built at runtime from pieces, so
# this file itself holds none of them and passes every check.
#
# Usage: bash scripts/test_check_public_tree.sh
# gitleaks cases run when gitleaks is on PATH; with REQUIRE_GITLEAKS=1 (CI) a missing
# gitleaks is a failure instead of a skip.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd -P)
check="$here/check_public_tree.sh"
config="$here/../.gitleaks.toml"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failures=0

# A directory with ordinary text, a placeholder Stripe ID the tests use, and a binary file.
clean_dir() {
  local d
  d=$(mktemp -d "$work/case.XXXXXX")
  mkdir -p "$d/docs"
  printf 'TaskBeavo\n' >"$d/README.md"
  printf 'account = "acct_1HelperSandbox00"\n' >"$d/docs/fixture.py"
  printf '\x89PNG\r\n\x1a\n\x00\x00\x00' >"$d/docs/logo.png"
  printf '%s' "$d"
}

expect() { # expect pass|fail NAME DIR
  local want=$1 name=$2 dir=$3 out rc
  out=$("$check" "$dir" 2>&1)
  rc=$?
  if { [ "$want" = pass ] && [ $rc -eq 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 1 ]; }; then
    echo "ok   $name"
  else
    echo "FAIL $name: expected $want, exit $rc"
    printf '%s\n' "$out" | sed 's/^/     | /'
    failures=$((failures + 1))
  fi
}

# Every check prints file names only, never the matched value.
expect_no_value() { # expect_no_value NAME DIR VALUE
  local name=$1 dir=$2 value=$3
  if "$check" "$dir" 2>&1 | grep -qF -- "$value"; then
    echo "FAIL $name: the output shows the matched value"
    failures=$((failures + 1))
  else
    echo "ok   $name"
  fi
}

d=$(clean_dir)
expect pass "a clean directory passes" "$d"

# 3d: A4 content
d=$(clean_dir)
printf 'See docs/decisions/%s\n' "A4-private-""helper-payer" >"$d/docs/notes.md"
expect fail "3d fails on a planted A4 reference" "$d"

# 3e: Stripe account IDs outside the placeholder allowlist
d=$(clean_dir)
planted_acct="acct_""1Q2w3E4r5T6y7U8i"
printf 'STRIPE_ACCOUNT = "%s"\n' "$planted_acct" >"$d/docs/settings.py"
expect fail "3e fails on a planted non-placeholder acct_ ID" "$d"
expect_no_value "3e prints the file, not the account ID" "$d" "$planted_acct"

d=$(clean_dir)
printf 'pmc = "%s"\n' "pmc_""1Q2w3E4r5T6y7U8i" >"$d/docs/config.py"
expect fail "3e fails on a payment method configuration ID" "$d"

# 3f: internal URLs and local paths, in text and binary files
d=$(clean_dir)
planted_path="/Use""rs/someone/project"
printf 'cd %s\n' "$planted_path" >"$d/docs/howto.md"
expect fail "3f fails on a planted local path" "$d"
expect_no_value "3f prints the file, not the path" "$d" "$planted_path"

d=$(clean_dir)
printf '\x00\x01%s\x00' "/ho""me/someone/build" >"$d/docs/compiled.mo"
expect fail "3f fails on a local path inside a binary file" "$d"

d=$(clean_dir)
printf 'https://%s/browse/X-1\n' "someone.atlass""ian.net" >"$d/docs/link.md"
expect fail "3f fails on an internal URL" "$d"

# 3h: build output, OS files, local databases and logs
for planted in .DS_Store debug.log local.sqlite3 data.db; do
  d=$(clean_dir)
  printf 'x' >"$d/docs/$planted"
  expect fail "3h fails on a planted $planted" "$d"
done
d=$(clean_dir)
mkdir -p "$d/backend/__pycache__"
printf 'x' >"$d/backend/__pycache__/models.cpython-312.pyc"
expect fail "3h fails on __pycache__ output" "$d"

# In a git checkout only tracked files count: an ignored or untracked local file passes,
# the same file committed fails.
if command -v git >/dev/null; then
  d=$(clean_dir)
  git -C "$d" init -q
  git -C "$d" add -A
  printf 'x' >"$d/.DS_Store"
  printf 'cd %s\n' "/Use""rs/someone" >"$d/untracked.md"
  expect pass "a git checkout ignores untracked files" "$d"
  git -C "$d" add -f .DS_Store untracked.md
  expect fail "a git checkout fails on the same files once tracked" "$d"
fi

# The excluded files name the patterns, so they are skipped (3d, 3f).
d=$(clean_dir)
printf '%s %s\n' "A4-private-""helper-payer" "/Use""rs/" >"$d/docs/public-release.md"
expect pass "docs/public-release.md may name the patterns" "$d"

# Only those exact paths: a file with the same name elsewhere is checked.
d=$(clean_dir)
mkdir -p "$d/apps/web"
printf 'cd %s\n' "/Use""rs/someone" >"$d/apps/web/public-release.md"
expect fail "3f checks a public-release.md outside docs/" "$d"
d=$(clean_dir)
mkdir -p "$d/apps"
printf '%s export-ignore\n' "A4-private-""helper-payer" >"$d/apps/.gitattributes"
expect fail "3d checks a .gitattributes outside the root" "$d"

# gitleaks: the CI scan of the tree and of the new commits, with the repository's config.
if command -v gitleaks >/dev/null; then
  token="ghp_$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 36)"
  gl() { gitleaks "$@" --config "$config" --redact --no-banner --log-level error >/dev/null 2>&1; }

  d=$(clean_dir)
  if gl dir "$d"; then echo "ok   gitleaks passes a clean directory"; else
    echo "FAIL gitleaks flags a clean directory"
    failures=$((failures + 1))
  fi
  printf 'GITHUB_TOKEN=%s\n' "$token" >"$d/docs/env.txt"
  if gl dir "$d"; then
    echo "FAIL gitleaks missed a planted token in the tree"
    failures=$((failures + 1))
  else echo "ok   gitleaks fails on a planted token in the tree"; fi

  # A token committed and then deleted is still in the pushed commits.
  d=$(clean_dir)
  g() { git -C "$d" -c user.name=test -c user.email=test@example.com "$@"; }
  g init -q && g add -A && g commit -qm base
  base=$(g rev-parse HEAD)
  printf 'GITHUB_TOKEN=%s\n' "$token" >"$d/docs/env.txt"
  g add -A && g commit -qm add
  g rm -q docs/env.txt && g commit -qm remove
  if gl git "$d" --log-opts="$base..HEAD"; then
    echo "FAIL gitleaks missed a token in the new commits"
    failures=$((failures + 1))
  else echo "ok   gitleaks fails on a token in the new commits, deleted later"; fi
  if gl git "$d" --log-opts="$base"; then echo "ok   gitleaks passes the clean base commit"; else
    echo "FAIL gitleaks flags the clean base commit"
    failures=$((failures + 1))
  fi
elif [ "${REQUIRE_GITLEAKS:-}" = 1 ]; then
  echo "FAIL gitleaks is not installed (REQUIRE_GITLEAKS=1)"
  failures=$((failures + 1))
else
  echo "skip gitleaks cases: gitleaks is not on PATH"
fi

if [ $failures -gt 0 ]; then
  echo "$failures failed"
  exit 1
fi
echo "all passed"
