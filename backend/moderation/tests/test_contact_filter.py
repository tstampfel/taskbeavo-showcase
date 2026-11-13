"""The contact-detail filter: what it masks, what it holds for review, what it leaves alone.

Each table row is (locale, input text, expected verdict, expected match kinds, expected
masked text or None when the text must come back unchanged).
"""

import logging
import time

import pytest

from moderation.contact_filter import (
    MASK,
    MAX_TEXT_LENGTH,
    MatchKind,
    Verdict,
    filter_contact_details,
)

PHONE = MatchKind.PHONE
EMAIL = MatchKind.EMAIL
URL = MatchKind.URL
MESSENGER = MatchKind.MESSENGER

CLEAN = Verdict.CLEAN
FLAGGED = Verdict.FLAGGED
MASKED = Verdict.MASKED
REVIEW = Verdict.NEEDS_REVIEW

M = MASK

CASES = [
    # --- Croatian: phones ---
    ("hr", "Nazovite me na 091 234 5678", MASKED, {PHONE}, f"Nazovite me na {M}"),
    ("hr", "Moj broj je 0912345678.", MASKED, {PHONE}, f"Moj broj je {M}."),
    ("hr", "Broj: +385 91 234 5678", MASKED, {PHONE}, f"Broj: {M}"),
    ("hr", "Broj: +385912345678", MASKED, {PHONE}, f"Broj: {M}"),
    ("hr", "zovi 00385 91 234 5678", MASKED, {PHONE}, f"zovi {M}"),
    ("hr", "zovi 0 9 1 2 3 4 5 6 7 8", MASKED, {PHONE}, f"zovi {M}"),
    ("hr", "Fiksni 020/123-456", MASKED, {PHONE}, f"Fiksni {M}"),
    ("hr", "Tel. (020) 123 456", MASKED, {PHONE}, f"Tel. {M}"),
    (
        "hr",
        "nula devet jedan dva tri četiri pet šest sedam",
        MASKED,
        {PHONE},
        M,
    ),
    ("hr", "zovi nula 9 1 dva 3 4 pet 6 7 8", MASKED, {PHONE}, f"zovi {M}"),
    ("hr", "javi se na 234 5678", REVIEW, {PHONE}, None),
    ("hr", "nula devet jedan dva tri", REVIEW, {PHONE}, None),
    # --- Croatian: emails, URLs, messengers ---
    ("hr", "Pišite na marko.horvat@example.com", MASKED, {EMAIL}, f"Pišite na {M}"),
    ("hr", "marko (at) example (dot) com", MASKED, {EMAIL}, M),
    ("hr", "marko [at] example [točka] com", MASKED, {EMAIL}, M),
    # A plain "et" counts as "@" next to a known mail provider. Gmail usernames have at
    # least six characters, so this one-letter address can't belong to anyone.
    ("hr", "x et gmail točka com", MASKED, {EMAIL}, M),
    ("hr", "marko.horvat et example točka com", MASKED, {EMAIL}, M),
    ("hr", "marko majmun example tocka com", MASKED, {EMAIL}, M),
    ("hr", "Pogledaj www.mojeusluge.hr za cijene", MASKED, {URL}, f"Pogledaj {M} za cijene"),
    ("hr", "Više na https://example.com/profil?id=3", MASKED, {URL}, f"Više na {M}"),
    ("hr", "Stranica: mojeusluge.hr", MASKED, {URL}, f"Stranica: {M}"),
    ("hr", "Javi se na WhatsApp 091 234 5678", MASKED, {PHONE, MESSENGER}, None),
    ("hr", "Imam Viber, javi se", FLAGGED, {MESSENGER}, None),
    ("hr", "insta: marko.dbk", MASKED, {MESSENGER}, f"insta: {M}"),
    ("hr", "Nađi me na Telegramu @marko_dbk", MASKED, {MESSENGER}, f"Nađi me na Telegramu {M}"),
    ("hr", "piši na wa.me/385912345678", MASKED, {URL, MESSENGER}, f"piši na {M}"),
    ("hr", "mojeusluge točka hr", REVIEW, {URL}, None),
    # --- Croatian: false positives ---
    ("hr", "Košnja trave, 150 € za 3 sata", CLEAN, set(), None),
    ("hr", "Budžet 1.250.000 kn za cijelu kuću", CLEAN, set(), None),
    ("hr", "Cijena 2.500,00 EUR", CLEAN, set(), None),
    ("hr", "Termin 12.03.2026. u 9:00", CLEAN, set(), None),
    ("hr", "Od 12. 3. 2026. do 15. 3. 2026.", CLEAN, set(), None),
    ("hr", "Ulica Ante Starčevića 24, 20000 Dubrovnik", CLEAN, set(), None),
    ("hr", "Stan na 3. katu, 75 m2, 2 sobe", CLEAN, set(), None),
    ("hr", "Treba mi pet ljudi i dva kombija, tri sata", CLEAN, set(), None),
    ("hr", "Posao od 9:00-12:00, pauza 10:30", CLEAN, set(), None),
    ("hr", "Mama je u Gružu, kod tržnice", CLEAN, set(), None),
    # --- German ---
    ("de", "Ruf mich an: 0171 1234567", MASKED, {PHONE}, f"Ruf mich an: {M}"),
    ("de", "Handy +49 (0) 171 123 45 67", MASKED, {PHONE}, f"Handy {M}"),
    (
        "de",
        "null eins sieben eins eins zwei drei vier fünf sechs",
        MASKED,
        {PHONE},
        M,
    ),
    ("de", "Mail an anna.mueller (ät) example (punkt) org", MASKED, {EMAIL}, f"Mail an {M}"),
    ("de", "anna.mueller at example punkt org", MASKED, {EMAIL}, M),
    ("de", "Schreib mir an anna@example.org", MASKED, {EMAIL}, f"Schreib mir an {M}"),
    ("de", "Infos unter www.hausmeister-anna.de", MASKED, {URL}, f"Infos unter {M}"),
    ("de", "Am besten über WhatsApp", FLAGGED, {MESSENGER}, None),
    # German false positives
    ("de", "Termin am 03.11.2026 um 14:30 Uhr", CLEAN, set(), None),
    ("de", "Budget 1.200 € für 4 Stunden", CLEAN, set(), None),
    ("de", "Hauptstraße 12a, 3. Stock, Wohnung 7", CLEAN, set(), None),
    ("de", "Ich bin um acht Uhr da, zwei Stunden", CLEAN, set(), None),
    ("de", "Mama wohnt in Gruž, z.B. beim Markt", CLEAN, set(), None),
    ("de", "Treffen at home, ca. 2 h", CLEAN, set(), None),
    # --- French ---
    ("fr", "Appelez-moi au 06 39 98 12 34", MASKED, {PHONE}, f"Appelez-moi au {M}"),
    ("fr", "Mon numéro : +33 6 39 98 12 34", MASKED, {PHONE}, f"Mon numéro : {M}"),
    (
        "fr",
        "zéro six un deux trois quatre cinq six sept huit",
        MASKED,
        {PHONE},
        M,
    ),
    ("fr", "jean.dupont arobase example point net", MASKED, {EMAIL}, M),
    ("fr", "Écrivez à jean.dupont@example.net", MASKED, {EMAIL}, f"Écrivez à {M}"),
    ("fr", "Voir mon site http://jardin-jean.fr", MASKED, {URL}, f"Voir mon site {M}"),
    ("fr", "Contactez-moi sur Telegram", FLAGGED, {MESSENGER}, None),
    # French false positives
    ("fr", "Rendez-vous le 03/11/2026 à 14h30", CLEAN, set(), None),
    ("fr", "Budget : 1 500 € pour la journée", CLEAN, set(), None),
    ("fr", "Prix 45,50 EUR, 2 heures", CLEAN, set(), None),
    ("fr", "12 rue de la Paix, 3e étage, appartement 21", CLEAN, set(), None),
    ("fr", "Un jardin neuf de six mètres et deux arbres", CLEAN, set(), None),
    ("fr", "Jean et Marie, point de rendez-vous au port", CLEAN, set(), None),
    # --- Review round 1 regressions ---
    # R1-1: a currency next to a phone number doesn't hide it; words ending in -eur don't either.
    ("hr", "Zovi 091 234 5678 €", MASKED, {PHONE}, f"Zovi {M} €"),
    ("hr", "Zovi 0912345678 kn", MASKED, {PHONE}, f"Zovi {M} kn"),
    ("de", "Ruf an 0171 1234567 Euro", MASKED, {PHONE}, f"Ruf an {M} Euro"),
    ("fr", "06 39 98 12 34 fr", MASKED, {PHONE}, f"{M} fr"),
    ("hr", "€ 091 234 5678", MASKED, {PHONE}, f"€ {M}"),
    ("hr", "Zovi 091 234 5678 eurovision", MASKED, {PHONE}, f"Zovi {M} eurovision"),
    ("fr", "Contactez le chauffeur 06 39 98 12 34", MASKED, {PHONE}, f"Contactez le chauffeur {M}"),
    ("de", "Der Monteur 0171 1234567", MASKED, {PHONE}, f"Der Monteur {M}"),
    ("hr", "Budžet 912345678 €", REVIEW, {PHONE}, None),
    # R1-2: digit groups split by line breaks, commas, symbols, joining words or invisible
    # characters.
    ("hr", "091\n234\n5678", REVIEW, {PHONE}, None),
    ("hr", "0\n9\n1\n2\n3\n4\n5\n6\n7\n8", REVIEW, {PHONE}, None),
    ("hr", "091,234,5678", REVIEW, {PHONE}, None),
    ("hr", "091_234_5678", REVIEW, {PHONE}, None),
    ("hr", "091 | 234 | 5678", REVIEW, {PHONE}, None),
    ("hr", "091:234:5678", REVIEW, {PHONE}, None),
    ("hr", "091 * 234 * 5678", REVIEW, {PHONE}, None),
    ("hr", "091 i 234 i 5678", REVIEW, {PHONE}, None),
    ("hr", "Zovi 091\u200b234\u200b5678", MASKED, {PHONE}, f"Zovi {M}"),
    (
        "hr",
        "Zovi \uff10\uff19\uff11 \uff12\uff13\uff14 \uff15\uff16\uff17\uff18",
        MASKED,
        {PHONE},
        f"Zovi {M}",
    ),
    ("hr", "Cijene: 10, 20, 30 i 40 €", CLEAN, set(), None),
    ("hr", "Termini 09:00, 10:00, 11:00 i 12:00", CLEAN, set(), None),
    # R1-3: French numbers with dots, dashes or slashes aren't dates.
    ("fr", "Appelez-moi au 06.12.34.56.78", MASKED, {PHONE}, f"Appelez-moi au {M}"),
    ("fr", "06-12-34-56-78", MASKED, {PHONE}, M),
    ("fr", "06/12/34/56/78", MASKED, {PHONE}, M),
    ("hr", "Zovi 09.12.34.56.78", MASKED, {PHONE}, f"Zovi {M}"),
    ("hr", "Zovi 01.02.03.04.05", MASKED, {PHONE}, f"Zovi {M}"),
    ("fr", "Le 06.12.24 à 14h30", CLEAN, set(), None),
    # R1-4: "install…" isn't Instagram.
    ("hr", "Treba mi instalater za bojler", CLEAN, set(), None),
    ("hr", "Instalacija klima uređaja", CLEAN, set(), None),
    ("de", "Installation einer Lampe", CLEAN, set(), None),
    ("fr", "Il faut installer un lavabo", CLEAN, set(), None),
    # R1-6: a missing space after a full stop is doubtful, not masked.
    ("fr", "Merci.De rien", REVIEW, {URL}, None),
    ("fr", "Bonjour.De plus, le jardin", REVIEW, {URL}, None),
    ("hr", "Hvala.Me zanima", REVIEW, {URL}, None),
    ("en", "Do.It now", REVIEW, {URL}, None),
    # R1-7: multi-part endings (masked whole, not cut at the inner ".co"), "(@)"/"(.)" and
    # fullwidth "@" (U+FF20).
    ("en", "marko@mail.co.example.com", MASKED, {EMAIL}, M),
    ("hr", "marko(@)example(.)com", MASKED, {EMAIL}, M),
    ("hr", "marko\uff20example.com", MASKED, {EMAIL}, M),
    ("en", "see ⓦⓦⓦ.example.com", MASKED, {URL}, f"see {M}"),
    # R1-8: short messenger names before a handle; ordinary words aren't messengers.
    ("en", "ig: marko.dbk", MASKED, {MESSENGER}, f"ig: {M}"),
    ("hr", "Moj IG: marko.dbk", MASKED, {MESSENGER}, f"Moj IG: {M}"),
    ("en", "my snap is marko_dbk", MASKED, {MESSENGER}, f"my snap is {M}"),
    ("en", "Instagrammable view", CLEAN, set(), None),
    ("en", "facebook marketplace furniture", CLEAN, set(), None),
    ("en", "a messenger bag", CLEAN, set(), None),
    ("en", "@home tomorrow", CLEAN, set(), None),
    ("hr", "Javi se na Viberu", FLAGGED, {MESSENGER}, None),
    # R1-10: a house number followed by a postcode, without a comma.
    ("hr", "ul. Ante Starčevića 24 20000 Dubrovnik", CLEAN, set(), None),
    # --- A bare messaging-app name is flagged for staff but published unchanged ---
    ("hr", "Instagram mi je super za slike", FLAGGED, {MESSENGER}, None),
    ("hr", "Pišite mi na WhatsApp", FLAGGED, {MESSENGER}, None),
    ("hr", "Dogovorit ćemo se preko WhatsAppa", FLAGGED, {MESSENGER}, None),
    ("de", "Ich habe kein WhatsApp", FLAGGED, {MESSENGER}, None),
    ("de", "Schreib mir auf Telegram", FLAGGED, {MESSENGER}, None),
    ("fr", "Je n'ai pas Instagram", FLAGGED, {MESSENGER}, None),
    ("fr", "Écrivez-moi sur WhatsApp", FLAGGED, {MESSENGER}, None),
    # ...but with a handle or number next to it, it is masked or held as before.
    ("hr", "Pišite mi na WhatsApp 091 234 5678", MASKED, {PHONE, MESSENGER}, None),
    ("hr", "Instagram: marko.dbk", MASKED, {MESSENGER}, f"Instagram: {M}"),
    ("hr", "Viber 234 5678", REVIEW, {PHONE, MESSENGER}, None),
    ("de", "WhatsApp: 0171 1234567", MASKED, {PHONE, MESSENGER}, f"WhatsApp: {M}"),
    ("de", "Telegram @anna_mueller", MASKED, {MESSENGER}, f"Telegram {M}"),
    ("fr", "WhatsApp au 06 39 98 12 34", MASKED, {PHONE, MESSENGER}, None),
    ("fr", "Instagram : jean.dupont", MASKED, {MESSENGER}, f"Instagram : {M}"),
]


@pytest.mark.parametrize(("locale", "text", "verdict", "kinds", "masked"), CASES)
def test_contact_filter_table(
    locale: str, text: str, verdict: Verdict, kinds: set[MatchKind], masked: str | None
) -> None:
    result = filter_contact_details(text, locale)

    assert result.verdict == verdict
    assert result.kinds == frozenset(kinds)
    if masked is not None:
        assert result.text == masked
    elif verdict is not Verdict.MASKED:
        # Clean and doubtful-only text comes back unchanged.
        assert result.text == text


def test_masks_every_match_and_counts_them() -> None:
    result = filter_contact_details(
        "Zovi 091 234 5678 ili piši na a@example.com ili 098 765 4321", "hr"
    )

    assert result.text == f"Zovi {M} ili piši na {M} ili {M}"
    assert result.verdict is Verdict.MASKED
    assert result.masked_count == 3
    assert result.review_count == 0


def test_doubtful_match_wins_over_masked_and_clear_ones_are_still_masked() -> None:
    result = filter_contact_details("Zovi 091 234 5678 ili 234 5678", "hr")

    assert result.verdict is Verdict.NEEDS_REVIEW
    assert result.text == f"Zovi {M} ili 234 5678"
    assert result.masked_count == 1
    assert result.review_count == 1
    assert result.kinds == frozenset({PHONE})


def test_masked_match_wins_over_a_bare_app_name() -> None:
    result = filter_contact_details("Zovi 091 234 5678 ili Viber", "hr")

    assert result.verdict is Verdict.MASKED
    assert result.text == f"Zovi {M} ili Viber"
    assert result.masked_count == 1
    assert result.review_count == 0
    assert result.flagged_count == 1
    assert result.kinds == frozenset({PHONE, MESSENGER})


def test_bare_app_name_is_flagged_not_held_and_not_masked() -> None:
    result = filter_contact_details("Pišite mi na WhatsApp", "hr")

    assert result.verdict is Verdict.FLAGGED
    assert result.text == "Pišite mi na WhatsApp"
    assert (result.masked_count, result.review_count, result.flagged_count) == (0, 0, 1)
    assert result.kinds == frozenset({MESSENGER})


def test_verdicts_are_ordered_mildest_first() -> None:
    assert list(Verdict) == [Verdict.CLEAN, Verdict.FLAGGED, Verdict.MASKED, Verdict.NEEDS_REVIEW]


def test_empty_text_is_clean() -> None:
    result = filter_contact_details("", "hr")

    assert result.verdict is Verdict.CLEAN
    assert result.text == ""
    assert result.kinds == frozenset()


@pytest.mark.parametrize("locale", ["de-AT", "HR", "es", ""])
def test_regional_and_unsupported_locales_are_accepted(locale: str) -> None:
    result = filter_contact_details("Nazovi 091 234 5678", locale)

    assert result.verdict is Verdict.MASKED


def test_spelled_digits_use_the_authors_locale() -> None:
    # "un deux trois" are digits to a French author, but not to a Croatian one.
    french = "zéro six un deux trois quatre cinq six sept huit"

    assert filter_contact_details(french, "fr").verdict is Verdict.MASKED
    assert filter_contact_details(french, "hr").verdict is Verdict.CLEAN


def test_never_logs_the_text(caplog: pytest.LogCaptureFixture) -> None:
    secret = "marko.horvat@example.com 091 234 5678"
    with caplog.at_level(logging.DEBUG):
        filter_contact_details(secret, "hr")

    assert "marko" not in caplog.text
    assert "234" not in caplog.text


@pytest.mark.parametrize(
    "text",
    [
        "a" * 20_000,
        "a." * 10_000,
        "a@" + "b" * 20_000,
        "@" + "a" * 20_000,
        "viber " + "." * 20_000,
        "1 " * 10_000,
        "a-" * 10_000,
        "www " + "a" * 20_000 + " dot",
        ("091 234 5678 x " * 1_400)[:20_000],
        ("@a_1 " * 4_000)[:20_000],
    ],
)
def test_runs_in_linear_time(text: str) -> None:
    started = time.perf_counter()
    filter_contact_details(text, "hr")

    assert time.perf_counter() - started < 0.2


def test_text_over_the_limit_is_held_for_review_unscanned() -> None:
    text = "x" * (MAX_TEXT_LENGTH + 1)

    result = filter_contact_details(text, "hr")

    assert result.verdict is Verdict.NEEDS_REVIEW
    assert result.text == text


def test_result_has_no_matched_text() -> None:
    # The result carries kinds and counts only, so a caller can't send the match to telemetry.
    result = filter_contact_details("091 234 5678", "hr")

    assert "234" not in repr(result).replace(result.text, "")
