"""The synthetic OIB and IBAN helpers, and the repository check that nothing else that
looks like a real OIB or IBAN is committed (scripts/check_synthetic_identifiers.py)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from core.synthetic_identifiers import synthetic_iban, synthetic_oib
from helpers.iban import normalize_iban
from helpers.oib import is_valid_oib

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECK_SCRIPT = REPO_ROOT / "scripts" / "check_synthetic_identifiers.py"


def test_a_synthetic_oib_is_eight_zeros_a_counter_and_a_valid_check_digit() -> None:
    oibs = [synthetic_oib(n) for n in range(100)]
    assert len(set(oibs)) == 100
    for n, oib in enumerate(oibs):
        assert is_valid_oib(oib)
        assert oib.startswith("00000000")
        assert oib[8:10] == f"{n:02d}"


def test_a_synthetic_oib_counter_has_two_digits() -> None:
    with pytest.raises(ValueError):
        synthetic_oib(100)
    with pytest.raises(ValueError):
        synthetic_oib(-1)


@pytest.mark.parametrize("country,length", [("HR", 21), ("DE", 22), ("AT", 20)])
def test_a_synthetic_iban_passes_the_eu_check_with_a_zero_account(
    country: str, length: int
) -> None:
    iban = synthetic_iban(7, country)
    assert len(iban) == length
    assert normalize_iban(iban) == iban
    assert iban.startswith(country)
    assert iban[4:] == "7".rjust(length - 4, "0")


def test_a_synthetic_iban_outside_the_eu_has_a_valid_mod_97_check() -> None:
    for country in ("CH", "GB"):
        iban = synthetic_iban(1, country)
        rearranged = iban[4:] + iban[:4]
        assert int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1
        # Not an EU account, so the payout form refuses it.
        assert normalize_iban(iban) is None


def test_synthetic_ibans_differ_by_counter() -> None:
    assert len({synthetic_iban(n) for n in range(1, 50)}) == 49


def test_a_synthetic_iban_needs_a_known_country_and_a_short_counter() -> None:
    with pytest.raises(ValueError):
        synthetic_iban(1, "XX")
    with pytest.raises(ValueError):
        synthetic_iban(10_000)


# --- The repository check -------------------------------------------------------------


def _load_check() -> ModuleType:
    if not CHECK_SCRIPT.exists():
        # The backend Docker image has only backend/, not the repository.
        pytest.skip("scripts/ is not next to backend/ (running inside the image)")
    spec = importlib.util.spec_from_file_location("check_synthetic_identifiers", CHECK_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def check() -> ModuleType:
    return _load_check()


def _oib(first_ten: str) -> str:
    """`first_ten` and its ISO 7064 MOD 11,10 check digit. The planted values are built
    here, so this file holds no checksum-valid OIB or IBAN for the check to find."""
    remainder = 10
    for digit in first_ten:
        remainder = (remainder + int(digit)) % 10 or 10
        remainder = (remainder * 2) % 11
    return first_ten + str((11 - remainder) % 10)


def _iban(country: str, bban: str) -> str:
    """`country`, valid mod-97 check digits and `bban`."""
    numeric = "".join(str(int(char, 36)) for char in bban + country + "00")
    return f"{country}{98 - int(numeric) % 97:02d}{bban}"


# Made up for these tests: real-shaped, with valid check digits, built at run time.
REAL_LOOKING_OIB = _oib("1234567890")
REAL_LOOKING_IBANS = [
    _iban("HR", "2390001" + "1234567890"),
    _iban("DE", "50010517" + "1234567890"),
    _iban("AT", "12000" + "12345678901"),
]


def _plant(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_repository_passes_the_check(check: ModuleType) -> None:
    findings = check.scan(REPO_ROOT, check.load_allowlist(REPO_ROOT / check.ALLOWLIST))
    assert findings == []


def test_the_check_passes_synthetic_and_invalid_identifiers(
    check: ModuleType, tmp_path: Path
) -> None:
    _plant(
        tmp_path,
        "fixtures.py",
        f'OIB = "{synthetic_oib(1)}"\nVAT = "HR{synthetic_oib(2)}"\n'
        f'IBAN = "{synthetic_iban(3)}"\nDE = "{synthetic_iban(4, "DE")}"\n'
        # Invalid check digits are not an OIB or IBAN: tests use them to exercise validation.
        'BAD_OIB = "12345678901"\nBAD_IBAN = "HR1200000000000000001"\n',
    )
    assert check.scan(tmp_path, {}) == []


def test_the_check_fails_on_a_planted_real_looking_oib(check: ModuleType, tmp_path: Path) -> None:
    real_looking = REAL_LOOKING_OIB
    _plant(tmp_path, "apps/web/e2e/spec.ts", f"const oib = '{real_looking}';\n")
    findings = check.scan(tmp_path, {})
    assert [(f.path, f.line, f.kind, f.value) for f in findings] == [
        ("apps/web/e2e/spec.ts", 1, "OIB", real_looking)
    ]


def test_the_check_finds_an_oib_inside_a_vat_id(check: ModuleType, tmp_path: Path) -> None:
    _plant(tmp_path, "a.md", f"VAT ID HR{REAL_LOOKING_OIB}\n")
    assert [f.value for f in check.scan(tmp_path, {})] == [REAL_LOOKING_OIB]


def _print_form(iban: str) -> str:
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


@pytest.mark.parametrize("text", [*REAL_LOOKING_IBANS, _print_form(REAL_LOOKING_IBANS[0])])
def test_the_check_fails_on_a_planted_real_looking_iban(
    check: ModuleType, tmp_path: Path, text: str
) -> None:
    _plant(tmp_path, "factories.py", f'iban = "{text}"\n')
    findings = check.scan(tmp_path, {})
    assert [f.kind for f in findings] == ["IBAN"]
    assert findings[0].value == text.replace(" ", "")


def test_an_allowlisted_value_passes(check: ModuleType, tmp_path: Path) -> None:
    _plant(tmp_path, "allow.txt", f"{REAL_LOOKING_OIB}  a made-up test value, with its reason\n")
    _plant(tmp_path, "a.py", f'x = "{REAL_LOOKING_OIB}"\n')
    allowlist = check.load_allowlist(tmp_path / "allow.txt")
    assert allowlist == {REAL_LOOKING_OIB: "a made-up test value, with its reason"}
    assert check.scan(tmp_path, allowlist) == []


def test_an_allowlist_entry_needs_a_reason(check: ModuleType, tmp_path: Path) -> None:
    _plant(tmp_path, "allow.txt", f"# comment\n\n{REAL_LOOKING_OIB}\n")
    with pytest.raises(ValueError, match="reason"):
        check.load_allowlist(tmp_path / "allow.txt")


def test_lockfiles_build_caches_and_binary_files_are_skipped(
    check: ModuleType, tmp_path: Path
) -> None:
    _plant(tmp_path, "backend/uv.lock", f"hash = sha256:ab{REAL_LOOKING_OIB}cd\n")
    _plant(tmp_path, "apps/web/tsconfig.tsbuildinfo", f'{{"h":"{REAL_LOOKING_OIB}"}}\n')
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00\xff" + REAL_LOOKING_OIB.encode())
    assert check.scan(tmp_path, {}) == []


def test_the_command_exits_non_zero_on_a_finding(check: ModuleType, tmp_path: Path) -> None:
    _plant(tmp_path, check.ALLOWLIST, "")
    assert check.main(["--root", str(tmp_path)]) == 0
    _plant(tmp_path, "a.py", f'x = "{REAL_LOOKING_OIB}"\n')
    assert check.main(["--root", str(tmp_path)]) == 1


def test_a_text_file_in_another_encoding_is_scanned(check: ModuleType, tmp_path: Path) -> None:
    # A CP1250 CSV fixture ("Šibenik" isn't valid UTF-8 there) still gets checked.
    (tmp_path / "helpers.csv").write_bytes(f"Šibenik;{REAL_LOOKING_OIB}\n".encode("cp1250"))
    assert [f.value for f in check.scan(tmp_path, {})] == [REAL_LOOKING_OIB]


# --- Email addresses ------------------------------------------------------------------

# A webmail address, built at run time so this file holds none for the check to find.
PLANTED_GMAIL = "ana.kovac" + "@" + "gmail.com"


def test_the_check_fails_on_a_planted_gmail_address(check: ModuleType, tmp_path: Path) -> None:
    _plant(tmp_path, "apps/web/e2e/sign-in.spec.ts", f"\n  email: '{PLANTED_GMAIL}',\n")
    findings = check.scan(tmp_path, {})
    assert [(f.path, f.line, f.kind, f.value) for f in findings] == [
        ("apps/web/e2e/sign-in.spec.ts", 2, "EMAIL", PLANTED_GMAIL)
    ]
    assert check.main(["--root", str(tmp_path)]) == 1


@pytest.mark.parametrize(
    "address",
    [
        "marko.horvat" + "@" + "web.de",
        "Ana.Kovac" + "@" + "Gmail.com",
        "x7k2p9" + "@" + "privaterelay.appleid.com",
        "a" + "@" + "b.hr",
        "ana.h" + "@" + "example.at",  # example.at is an ordinary registrable domain
        "sie" + "@" + "beispiel.de",
        "marko" + "\uff20" + "gmail.com",  # fullwidth commercial at, as the filter tests use
        "anna" + "@" + "example.com.evil.de",  # reserved name, but not the reserved domain
    ],
)
def test_the_check_fails_on_an_address_outside_the_reserved_domains(
    check: ModuleType, tmp_path: Path, address: str
) -> None:
    _plant(tmp_path, "a.py", f'email = "{address}"\n')
    assert [(f.kind, f.value) for f in check.scan(tmp_path, {})] == [("EMAIL", address)]


def test_the_check_passes_reserved_domains_and_non_addresses(
    check: ModuleType, tmp_path: Path
) -> None:
    _plant(
        tmp_path,
        "fixtures.py",
        # RFC 2606: example.com/.org/.net and their subdomains, and the reserved TLDs.
        'A = "ana.kovac@example.com"\nB = "Ana.Kovac@Example.COM"\nC = "x@demo.example.com"\n'
        'D = "j@example.org"\nE = "k@example.net"\nF = "sam@taskbeavo.test"\n'
        'G = "p@shop.example"\nH = "q@db.invalid"\nI = "r@host.localhost"\n'
        'J = "marko\uff20example.com"\n'
        # URL userinfo (Sentry DSNs, database URLs) is not an email address.
        'DSN = "https://key@o1.ingest.de.sentry.io/1"\n'
        'DB = "postgres://user:s3cret@ep-x-pooler.eu-central-1.aws.neon.tech/db"\n'
        # A domain alone, a handle and a docstring's elided address are not addresses.
        "# Apple relays mail through ...@privaterelay.appleid.com\n"
        'HANDLE = "@marko_dbk"\nDECORATOR = "@pytest.fixture"\n',
    )
    assert check.scan(tmp_path, {}) == []


def test_an_allowlisted_address_passes(check: ModuleType, tmp_path: Path) -> None:
    _plant(tmp_path, "allow.txt", f"{PLANTED_GMAIL} a made-up test value, with its reason\n")
    _plant(tmp_path, "a.py", f'x = "{PLANTED_GMAIL}"\n')
    assert check.scan(tmp_path, check.load_allowlist(tmp_path / "allow.txt")) == []
