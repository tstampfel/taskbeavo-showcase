"""The committed gettext catalogs are complete and their compiled .mo files are current.

The .mo files are committed because the image has no gettext; after editing a .po run
`uv run python manage.py compilemessages` (needs GNU gettext on the host).
"""

import ast
import gettext
from pathlib import Path

import pytest
from django.conf import settings

LOCALE_DIR = Path(settings.BASE_DIR) / "locale"
TRANSLATED = ["hr", "de", "fr"]


def po_entries(path: Path) -> dict[str, str]:
    """msgid → msgstr for every entry except the header. Plural forms aren't used yet."""
    entries: dict[str, str] = {}
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        parts = {"msgid": "", "msgstr": ""}
        field = None
        fuzzy = False
        for line in block.splitlines():
            if line.startswith("#,") and "fuzzy" in line:
                fuzzy = True
            if line.startswith("#"):
                continue
            keyword, _, rest = line.partition(" ")
            if keyword in parts:
                field, line = keyword, rest
            elif keyword.startswith("msg"):
                pytest.fail(f"{path}: unsupported {keyword}; add plural support to this test")
            if field:
                parts[field] += ast.literal_eval(line)
        if parts["msgid"]:
            assert not fuzzy, f"{path}: fuzzy entry {parts['msgid']!r}"
            entries[parts["msgid"]] = parts["msgstr"]
    return entries


@pytest.mark.parametrize("locale", ["hr", "en", "de", "fr"])
def test_catalog_exists_for_every_language(locale: str) -> None:
    assert (LOCALE_DIR / locale / "LC_MESSAGES" / "django.po").is_file()
    assert (LOCALE_DIR / locale / "LC_MESSAGES" / "django.mo").is_file()


def test_every_language_has_the_same_msgids() -> None:
    ids = {
        locale: set(po_entries(LOCALE_DIR / locale / "LC_MESSAGES" / "django.po"))
        for locale, _name in settings.LANGUAGES
    }
    assert ids["en"], "the English catalog is empty"
    for locale, msgids in ids.items():
        assert msgids == ids["en"], f"{locale} differs from en"


@pytest.mark.parametrize("locale", TRANSLATED)
def test_every_message_is_translated_and_not_fuzzy(locale: str) -> None:
    entries = po_entries(LOCALE_DIR / locale / "LC_MESSAGES" / "django.po")

    assert [msgid for msgid, msgstr in entries.items() if not msgstr] == []


@pytest.mark.parametrize("locale", ["hr", "en", "de", "fr"])
def test_compiled_catalog_matches_the_po_file(locale: str) -> None:
    folder = LOCALE_DIR / locale / "LC_MESSAGES"
    with (folder / "django.mo").open("rb") as mo:
        compiled = gettext.GNUTranslations(mo)

    for msgid, msgstr in po_entries(folder / "django.po").items():
        if msgstr:
            assert compiled.gettext(msgid) == msgstr, f"{locale}: run compilemessages"
