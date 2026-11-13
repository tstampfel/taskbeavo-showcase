#!/usr/bin/env bash
# The public-tree checks that need no private input (docs/public-release.md, step 3):
#   3d  no A4 content
#   3e  only placeholder Stripe account and configuration IDs
#   3f  no internal URLs or local paths, in text and binary files
#   3h  no build output, OS files, local databases or logs; lists the binary files
# CI (the "Public tree" job), the export steps and the local pre-push hook all run this
# one script, so the patterns live only here. 3c (the private identifiers list) and 3g
# (scripts/check_synthetic_identifiers.py) run separately.
#
# Usage: scripts/check_public_tree.sh [DIR]   (default: .)
# In a git checkout it reads the tracked files (as they are in the working tree); in a
# plain directory, such as a `git archive` export, every file. It prints file names
# only, never the matched values. Exit 0: every check passed; 1: a check failed; 2: error.
set -uo pipefail

root=${1:-.}
cd "$root" 2>/dev/null || {
  echo "ERROR: cannot read $root" >&2
  exit 2
}

# docs/public-release.md names the folder and paths in prose, so it skips 3d and 3f, as
# does the root .gitattributes for 3d. Exact paths from the repository root: a file with
# the same name elsewhere is still checked. This script is checked like any other file:
# a one-character class such as paye[r] matches the same text as the plain word but
# keeps the literal out of the source, so the script (and the pre-push guard) pass it.
DOC=docs/public-release.md

work=$(mktemp -d) || exit 2
trap 'rm -rf "$work"' EXIT
files="$work/files"

top=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -n "$top" ] && [ "$(cd "$top" && pwd -P)" = "$(pwd -P)" ]; then
  git ls-files -z >"$work/tracked" || exit 2
  while IFS= read -r -d '' f; do
    if [ -f "$f" ]; then printf '%s\0' "$f"; fi # skip tracked files deleted in the working tree
  done <"$work/tracked" >"$files"
else
  find . -type f ! -path './.git/*' -print0 | while IFS= read -r -d '' f; do
    printf '%s\0' "${f#./}"
  done >"$files"
fi

# without PATH... : the file list, minus these paths (relative to the root)
without() {
  local f x skip
  while IFS= read -r -d '' f; do
    skip=
    for x in "$@"; do [ "$f" = "$x" ] && skip=1; done
    [ -z "$skip" ] && printf '%s\0' "$f"
  done <"$files"
  return 0
}

# search GREP_ARGS... : grep over the file list read from stdin. Prints grep's output;
# exits 2 when grep fails on a file (exit status above 1).
search() {
  local out="$work/search.$RANDOM"
  xargs -0 sh -c 'grep "$@" </dev/null; [ $? -le 1 ] || exit 255' search "$@" >"$out"
  local rc=$?
  cat "$out"
  [ $rc -eq 0 ] || return 2
}

failed=0
errored=0
report() { # report ID DESCRIPTION MATCHES SEARCH_STATUS
  if [ "$4" -ne 0 ]; then
    echo "ERROR $1: grep failed"
    errored=1
  elif [ -n "$3" ]; then
    echo "FAIL $1: $2"
    printf '%s\n' "$3" | sed 's/^/  /'
    failed=1
  else
    echo "PASS $1"
  fi
}

# 3d. No A4 content
m=$(without "$DOC" .gitattributes |
  search -lIiE 'A4-private-helper-paye[r]|binding-opinion-reques[t]|jamac plata[c]|Pitanje [1-4]\.')
report 3d "A4 content in" "$m" $?

# 3e. Only placeholder Stripe account and configuration IDs
STRIPE_ID='\b(acct|pmc)_[A-Za-z0-9]{8,}'
PLACEHOLDER='acct_1(032D82eZvKYlo2C|HelperSandbox00|PlatformSandbox0|NotTheirs|SomebodyElse|SomeoneElse|TestHelper|TestHelperExpress|TheHelpersOwn)'
candidates=$(without | search -lIE "$STRIPE_ID")
rc=$?
m=
if [ $rc -eq 0 ] && [ -n "$candidates" ]; then
  while IFS= read -r f; do
    if grep -oIE "$STRIPE_ID" -- "$f" | grep -vqxE "$PLACEHOLDER"; then
      m="${m:+$m$'\n'}$f"
    fi
  done <<<"$candidates"
fi
report 3e "Stripe IDs outside the placeholder allowlist in" "$m" $rc

# 3f. No internal URLs or local paths, in text and binary files
m=$(without "$DOC" |
  search -laE 'atlassian\.net|sentry\.io/(organizations|settings|issues)|ui\.honeycomb\.io|dashboard\.(render|stripe)\.com|vercel\.com/[a-z0-9-]+/[a-z0-9-]+|expo\.dev/(accounts|@)|/User[s]/|/home/[a-z]+|\.claude/project[s]')
report 3f "internal URLs or local paths in" "$m" $?

# 3h. No build output, OS files, local databases or logs; lists the binary files to look at
m=$(while IFS= read -r -d '' f; do
  case "/$f" in
  (*/__pycache__/* | *.pyc | */.DS_Store | *.sqlite | *.sqlite3 | *.db | *.log) printf '%s\n' "$f" ;;
  esac
done <"$files")
report 3h "build output or local data in" "$m" 0
if [ -z "$m" ]; then
  without | search -lI '' | sort >"$work/text" || errored=1
  while IFS= read -r -d '' f; do if [ -s "$f" ]; then printf '%s\n' "$f"; fi; done <"$files" |
    sort | comm -23 - "$work/text" >"$work/binary"
  if [ -s "$work/binary" ]; then
    echo "  binary files to check are meant to be public:"
    sed 's/^/  /' "$work/binary"
  fi
fi

[ $errored -eq 0 ] || exit 2
exit $failed
