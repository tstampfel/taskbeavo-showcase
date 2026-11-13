"""Every notification kind is translated, fits a lock-screen preview and links to a screen."""

import re

import pytest
from django.utils import translation

from notifications.kinds import KINDS

# About what iOS and Android show of a title before cutting it off.
TITLE_MAX = 40


@pytest.mark.parametrize("locale", ["hr", "de", "fr"])
@pytest.mark.parametrize("key", sorted(KINDS))
def test_kind_text_is_translated(key: str, locale: str) -> None:
    kind = KINDS[key]
    with translation.override("en"):
        english = (str(kind.title), str(kind.body))
    with translation.override(locale):
        assert (str(kind.title), str(kind.body)) != english
        assert str(kind.title) != english[0]
        assert str(kind.body) != english[1]


@pytest.mark.parametrize("locale", ["hr", "en", "de", "fr"])
@pytest.mark.parametrize("key", sorted(KINDS))
def test_push_titles_fit_the_lock_screen(key: str, locale: str) -> None:
    with translation.override(locale):
        assert len(str(KINDS[key].title)) <= TITLE_MAX


# The paths the apps open from a link (APP_ROUTES in packages/app-links). The web serves the
# same paths under a locale, and the association files send them to the app.
LINKED_ROUTE = re.compile(
    r"^/(tasks/\{\w+\}|bookings/\{\w+\}(/release|/no-access|/arrival)?|conversations/\{\w+\}"
    r"|helper/payouts)$"
)


@pytest.mark.parametrize("key", sorted(KINDS))
def test_routes_are_linked_app_paths(key: str) -> None:
    assert LINKED_ROUTE.match(KINDS[key].route)


def test_payment_requested_opens_the_release_screen() -> None:
    # The screen only opens: releasing still needs the poster's tap and the server's checks.
    assert KINDS["booking.payment_requested"].route == "/bookings/{id}/release"


def test_on_my_way_push_never_promises_an_arrival_time() -> None:
    # The ETA on "On my way" is optional (BEAV-202): the push must read right without one,
    # so it says the helper set off and leaves the time to the app.
    with translation.override("en"):
        body = str(KINDS["booking.helper_on_my_way"].body)
    assert body == "Your helper set off. Open TaskBeavo to follow their arrival."
