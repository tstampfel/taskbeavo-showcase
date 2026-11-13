"""The server's category list and the excluded-category screening (trust-safety section 1)."""

import pytest

from core.errors import ApiError
from tasks.categories import ExcludedCategory, require_allowed_category, screen_excluded

RIDES = ExcludedCategory.RIDES
MEDICAL = ExcludedCategory.MEDICAL_CARE
CARE = ExcludedCategory.PERSONAL_CARE


@pytest.mark.parametrize("category", ["GARDEN", "ERRANDS", "REPAIRS", "CLEANING", "CHECK_IN"])
def test_the_launch_categories_are_allowed(category: str) -> None:
    assert require_allowed_category(category).value == category


@pytest.mark.parametrize(
    "category", ["RIDES", "MEDICAL_CARE", "PERSONAL_CARE", "TRANSPORT", "garden", ""]
)
def test_excluded_and_unknown_categories_are_refused(category: str) -> None:
    with pytest.raises(ApiError) as raised:
        require_allowed_category(category)
    assert raised.value.extensions == {"code": "CATEGORY_NOT_ALLOWED"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Rides: driving a person somewhere.
        ("Please drive my mother to the doctor on Monday", RIDES),
        ("Need someone to give mum a lift, she lives alone", RIDES),
        ("Odvesti mamu kod doktora u Dom zdravlja", RIDES),
        ("Trebam prijevoz za baku", RIDES),
        ("Bitte meine Mutter zum Arzt fahren", RIDES),
        ("Conduire ma mère chez le médecin", RIDES),
        # Medical care.
        ("Give her the insulin injection every evening", MEDICAL),
        ("Dati inzulin tati navečer", MEDICAL),
        ("Insulin spritzen bei meinem Vater", MEDICAL),
        ("Changer le pansement de mon père", MEDICAL),
        ("Wundversorgung am Bein", MEDICAL),
        # Personal care.
        ("Help bathe mum, she can't manage alone. Wash mother too", CARE),
        ("Okupati baku jednom tjedno", CARE),
        ("Meiner Mutter beim Duschen helfen", CARE),
        ("Habiller ma mère le matin", CARE),
        ("Osobna higijena za djeda", CARE),
    ],
)
def test_excluded_requests_are_found(text: str, expected: ExcludedCategory) -> None:
    assert expected in screen_excluded(text)


@pytest.mark.parametrize(
    "text",
    [
        # Errands where the helper drives alone, and accompaniment, are allowed.
        "Drive to the pharmacy and pick up mum's medicine",
        "Pick up insulin and the prescription from the doctor",
        "Take mum to the doctor by bus and wait with her",
        "Otpratiti mamu autobusom do bolnice",
        "Medikamente aus der Apotheke abholen und zum Arzt bringen",
        "Accompagner ma mère en taxi chez le médecin",
        # Ordinary tasks that share words with the rules.
        "Wash mum's car and the windows",
        "Oprati prozore i očistiti balkon",
        "Change the light bulbs in the hallway",
        "Kupiti pelene i namirnice u Konzumu",
        "Den Rasen mähen und die Hecke schneiden",
        "Faire les courses et arroser les plantes",
    ],
)
def test_allowed_tasks_are_not_flagged(text: str) -> None:
    assert screen_excluded(text) == frozenset()


def test_a_rule_needs_its_words_in_one_sentence() -> None:
    assert screen_excluded("I drive a lot. My mother lives near the hospital.") == frozenset()


def test_diacritics_and_case_dont_hide_a_match() -> None:
    assert screen_excluded("ODVESTI MAMU U BOLNICU") == {RIDES}
    assert screen_excluded("Conduire maman à l'hôpital") == {RIDES}
