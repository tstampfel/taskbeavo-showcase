"""The checkout tick-box texts marked in a CANCELLATION document (BEAV-111)."""

import pytest

from legal.checkout_consent import (
    CheckoutConsentText,
    CheckoutConsentTexts,
    checkout_consent_texts,
    variant_for,
    without_markers,
)
from legal.services import PublishRefused
from legal.tests.factories import cancellation_bodies, cancellation_body, publish_version

BODY = cancellation_body("en")


def test_both_pairs_are_found_with_their_whitespace_folded() -> None:
    body = BODY.replace(
        "Start early, please (trader, en).", "Start early,\n   please (trader, en)."
    )

    assert checkout_consent_texts(body) == CheckoutConsentTexts(
        private=CheckoutConsentText(
            cancellation_policy="I accept the policy (private, en).",
            early_start="Start early, please (private, en).",
        ),
        trader=CheckoutConsentText(
            cancellation_policy="I accept the policy (trader, en).",
            early_start="Start early, please (trader, en).",
        ),
    )


def test_the_pair_follows_the_helper_type_shown() -> None:
    texts = checkout_consent_texts(BODY)

    assert texts is not None
    assert texts.for_variant(variant_for(True)).early_start == "Start early, please (trader, en)."
    assert texts.for_variant(variant_for(False)).early_start == "Start early, please (private, en)."


@pytest.mark.parametrize(
    "body",
    [
        "No markers at all",
        BODY.replace("<!-- checkout:early_start_trader -->", ""),
        BODY + "<!-- checkout:early_start_private -->Again<!-- /checkout:early_start_private -->",
        BODY.replace("I accept the policy (private, en).", "   "),
        # The single-pair markup from before the helper-type split.
        "<!-- checkout:cancellation_policy -->A<!-- /checkout:cancellation_policy -->"
        "<!-- checkout:early_start -->B<!-- /checkout:early_start -->",
    ],
    ids=["none", "unclosed", "twice", "empty", "one-pair"],
)
def test_a_missing_duplicated_or_empty_text_is_no_text(body: str) -> None:
    assert checkout_consent_texts(body) is None


def test_readers_see_the_text_without_the_markers() -> None:
    shown = without_markers(BODY)

    assert "<!--" not in shown
    assert "I accept the policy (private, en)." in shown
    assert "Start early, please (trader, en)." in shown


@pytest.mark.django_db
def test_a_cancellation_policy_without_the_tick_box_texts_is_refused() -> None:
    texts = cancellation_bodies() | {"de": "Stornobedingungen ohne Ankreuztexte"}

    with pytest.raises(PublishRefused, match="missing in: de"):
        publish_version("CANCELLATION", "2026-11-c", bodies=texts)


@pytest.mark.django_db
def test_a_cancellation_policy_with_only_the_private_pair_is_refused() -> None:
    texts = {
        code: body.split("<!-- checkout:cancellation_policy_trader -->")[0]
        for code, body in cancellation_bodies().items()
    }

    with pytest.raises(PublishRefused, match="checkout:early_start_trader"):
        publish_version("CANCELLATION", "2026-11-c", bodies=texts)


@pytest.mark.django_db
def test_a_draft_cancellation_policy_needs_them_too() -> None:
    with pytest.raises(PublishRefused, match="checkout:early_start_private"):
        publish_version(
            "CANCELLATION",
            "draft-1",
            is_draft=True,
            bodies={code: "DRAFT" for code in cancellation_bodies()},
        )
