#!/usr/bin/env python3
"""Fail when the repository holds an OIB, IBAN or email address that could be real.

Tests, fixtures, the demo seed, docs and mockups use only visibly synthetic identifiers
(backend/core/synthetic_identifiers.py):

- an OIB is eight zeros, a two-digit counter and the check digit (00000000NNC);
- an IBAN's account part is all zeros and a counter of at most four digits;
- an email address is on a domain RFC 2606 reserves: example.com, example.org,
  example.net or a subdomain of one, or a name under .test, .example, .invalid or
  .localhost.

This finds every 11-digit number with a valid ISO 7064 MOD 11,10 check digit that
doesn't start with eight zeros, and every IBAN with valid mod-97 check digits whose
account part isn't zeros and a short counter. Values with a wrong check digit (tests
that exercise validation use them) are not OIBs or IBANs and pass. It also finds every
email address, written with "@" or the fullwidth "\uff20", on any other domain. The user
part of a URL (a Sentry DSN, a database URL) is not an address and passes; so does a
spelled-out address ("marko (at) example (dot) com"), which the check doesn't read. Any
exception goes in the allowlist file, one value and its reason per line.

Usage: python3 scripts/check_synthetic_identifiers.py [--root DIR]
Standard library only, so it runs on a plain checkout or a `git archive` export.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOWLIST = "scripts/synthetic-identifiers-allowlist.txt"

SYNTHETIC_OIB_PREFIX = "00000000"
MAX_SYNTHETIC_COUNTER = 9999

# Generated files full of hashes, where an 11-digit run is chance, not an OIB.
SKIPPED_NAMES = {"uv.lock", "pnpm-lock.yaml", "package-lock.json", "yarn.lock"}
SKIPPED_SUFFIXES = (".tsbuildinfo", ".map")
# Not walked when the root isn't a git checkout (an export has none of these).
SKIPPED_DIRS = {
    ".git", "node_modules", ".venv", ".turbo", ".next", "dist", "build", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".expo", "ios", "android", "playwright-report",
    "test-results", ".reviews",  # review reports: gitignored, not in the export
}  # fmt: skip

# IBAN lengths by country (SWIFT IBAN registry): Europe and its neighbours.
IBAN_LENGTHS = {
    "AD": 24, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22,
    "BY": 28, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "EE": 20, "EG": 29,
    "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18,
    "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IS": 26, "IT": 27, "JO": 30,
    "KW": 30, "KZ": 20, "LB": 28, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27,
    "MD": 24, "ME": 22, "MK": 19, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PS": 29,
    "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SE": 24, "SI": 19, "SK": 24,
    "SM": 27, "TN": 24, "TR": 26, "UA": 29, "AE": 23, "XK": 20,
}  # fmt: skip

_OIB = re.compile(r"(?<!\d)\d{11}(?!\d)")
# Electronic form or print form (groups separated by single spaces).
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}")
# The local part starts with a word character, so "...@domain" in prose is not an address.
_EMAIL = re.compile(
    r"(?<![\w.%+-])[\w%+-][\w.%+-]*[@\uff20](?P<domain>(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![\w-])"
)
RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_EMAIL_TLDS = ("test", "example", "invalid", "localhost")
# What ends the token an address sits in; a "://" before it in the token makes it a URL.
_TOKEN_BREAK = re.compile(r"[\s\"'`(<\[{=,]")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    value: str


def is_valid_oib(value: str) -> bool:
    remainder = 10
    for digit in value[:-1]:
        remainder = (remainder + int(digit)) % 10 or 10
        remainder = (remainder * 2) % 11
    return (11 - remainder) % 10 == int(value[-1])


def _mod97_ok(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def is_synthetic_iban(iban: str) -> bool:
    bban = iban[4:]
    return bban.isdigit() and int(bban) <= MAX_SYNTHETIC_COUNTER


def _ibans(text: str):
    for match in _IBAN.finditer(text):
        compact = match.group().replace(" ", "")
        length = IBAN_LENGTHS.get(compact[:2])
        if length is None or len(compact) < length:
            continue
        candidate = compact[:length]
        if _mod97_ok(candidate):
            yield match.start(), candidate


def is_reserved_email_domain(domain: str) -> bool:
    domain = domain.lower()
    if domain.rsplit(".", 1)[-1] in RESERVED_EMAIL_TLDS:
        return True
    return any(domain == d or domain.endswith("." + d) for d in RESERVED_EMAIL_DOMAINS)


def _emails(text: str):
    for match in _EMAIL.finditer(text):
        if is_reserved_email_domain(match.group("domain")):
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        before = text[line_start : match.start()]
        token = _TOKEN_BREAK.split(before)[-1]
        if "://" in token:
            continue  # the user part of a URL
        yield match.start(), match.group()


def scan_text(path: str, text: str, allowlist: dict[str, str]) -> list[Finding]:
    found: list[tuple[int, str, str]] = []
    for match in _OIB.finditer(text):
        value = match.group()
        if value.startswith(SYNTHETIC_OIB_PREFIX) or not is_valid_oib(value):
            continue
        found.append((match.start(), "OIB", value))
    for start, iban in _ibans(text):
        if not is_synthetic_iban(iban):
            found.append((start, "IBAN", iban))
    for start, email in _emails(text):
        found.append((start, "EMAIL", email))
    return [
        Finding(path, text.count("\n", 0, start) + 1, kind, value)
        for start, kind, value in sorted(found)
        if value not in allowlist
    ]


def _files(root: Path) -> list[str]:
    # A checkout scans the tracked files; an export (or a machine without git) scans the
    # whole tree minus SKIPPED_DIRS.
    git = shutil.which("git")
    if (root / ".git").exists() and git:
        listed = subprocess.run(  # noqa: S603 (fixed arguments, no shell)
            [git, "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        ).stdout.decode()
        return sorted(name for name in listed.split("\0") if name)
    names = []
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in SKIPPED_DIRS]
        for name in files:
            names.append(os.path.relpath(os.path.join(directory, name), root))
    return sorted(Path(name).as_posix() for name in names)


def scan(root: Path, allowlist: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    for name in _files(root):
        if Path(name).name in SKIPPED_NAMES or name.endswith(SKIPPED_SUFFIXES) or name == ALLOWLIST:
            continue
        path = root / name
        if not path.is_file():
            continue  # deleted in the work tree but still in the index
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary (images, fonts, compiled catalogues)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            # Text in another encoding (a Latin-1 or CP1250 CSV, say): digits and IBAN
            # letters are ASCII in all of them, so Latin-1 finds them without failing.
            text = data.decode("latin-1")
        findings.extend(scan_text(name, text, allowlist))
    return findings


def load_allowlist(path: Path) -> dict[str, str]:
    """`value reason…` per line; blank lines and `#` comments are ignored."""
    allowlist: dict[str, str] = {}
    if not path.exists():
        return allowlist
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        value, _, reason = line.partition(" ")
        if not reason.strip():
            raise ValueError(f"{path}:{number}: an allowlisted value needs a reason")
        allowlist[value] = reason.strip()
    return allowlist


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=Path(__file__).resolve().parent.parent, type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    findings = scan(root, load_allowlist(root / ALLOWLIST))
    for finding in findings:
        print(
            f"{finding.path}:{finding.line}: {finding.kind} {finding.value} looks real; "
            "use backend/core/synthetic_identifiers.py (an address on example.com for an "
            "email) or allowlist it with a reason"
        )
    if findings:
        print(f"FAIL: {len(findings)} real-looking OIB, IBAN or email value(s)", file=sys.stderr)
        return 1
    print("PASS: only synthetic OIBs and IBANs, and email addresses on reserved domains")
    return 0


if __name__ == "__main__":
    sys.exit(main())
