"""Access tokens and refresh rotation with reuse detection (accounts/sessions.py)."""

import datetime as dt
import re
from typing import Any
from urllib.parse import unquote

import jwt
import pytest
from asgiref.sync import async_to_sync
from django.core import mail
from django.utils import timezone

from accounts import geo, sessions
from accounts.models import RefreshToken, Session
from accounts.tests.factories import make_user, sign_in
from core.errors import ApiError, ErrorCode
from core.models import Outbox
from core.outbox import outbox_key
from notifications.models import PushToken

pytestmark = pytest.mark.django_db


def authenticate(token: str, *types: str):
    return async_to_sync(sessions.authenticate)(token, types or (sessions.ACCESS,))


def refresh_fails(refresh_token: str) -> None:
    with pytest.raises(ApiError) as excinfo:
        sessions.refresh_session(refresh_token)
    assert excinfo.value.code is ErrorCode.SESSION_EXPIRED


def test_access_token_is_a_short_jwt_naming_user_and_session(settings) -> None:
    user = make_user()
    issued = sign_in(user)

    claims = jwt.decode(
        issued.access_token,
        settings.JWT_SIGNING_KEY,
        algorithms=["HS256"],
        audience="taskbeavo-api",
    )
    assert claims["sub"] == str(user.pk)
    assert claims["sid"] == str(issued.session.pk)
    assert claims["typ"] == "access"
    assert claims["exp"] - claims["iat"] == settings.ACCESS_TOKEN_TTL_SECONDS == 900
    assert authenticate(issued.access_token) == (user, issued.session)


def test_refresh_rotates_both_tokens() -> None:
    user = make_user()
    first = sign_in(user)

    second = sessions.refresh_session(first.refresh_token)

    assert second.session == first.session
    assert second.refresh_token != first.refresh_token
    assert second.access_token != first.access_token
    assert authenticate(second.access_token) == (user, first.session)
    stored = RefreshToken.objects.order_by("created_at")
    assert [t.used_at is not None for t in stored] == [True, False]
    assert second.refresh_token not in {t.token_hash for t in stored}


def test_reused_refresh_token_revokes_the_whole_family(settings) -> None:
    settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS = 0
    first = sign_in(make_user())
    second = sessions.refresh_session(first.refresh_token)
    RefreshToken.objects.filter(used_at__isnull=False).update(
        used_at=timezone.now() - dt.timedelta(seconds=1)
    )

    refresh_fails(first.refresh_token)

    assert Session.objects.get().revoked_at is not None
    # The legitimate holder's newest tokens die with the family.
    refresh_fails(second.refresh_token)
    assert authenticate(second.access_token) is None


def test_reuse_within_the_grace_period_refreshes_without_revoking(settings) -> None:
    """Parallel browser requests racing to refresh with the same cookie."""
    settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS = 10
    first = sign_in(make_user())
    sessions.refresh_session(first.refresh_token)

    again = sessions.refresh_session(first.refresh_token)

    assert Session.objects.get().revoked_at is None
    assert authenticate(again.access_token) is not None


def test_reuse_does_not_revoke_other_sessions(settings) -> None:
    settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS = 0
    user = make_user()
    phone, laptop = sign_in(user), sign_in(user, Session.Platform.WEB)
    sessions.refresh_session(phone.refresh_token)
    RefreshToken.objects.filter(used_at__isnull=False).update(
        used_at=timezone.now() - dt.timedelta(seconds=1)
    )

    refresh_fails(phone.refresh_token)

    assert authenticate(laptop.access_token) is not None
    sessions.refresh_session(laptop.refresh_token)


def test_unknown_and_expired_refresh_tokens_are_refused() -> None:
    issued = sign_in(make_user())
    RefreshToken.objects.update(expires_at=timezone.now())

    refresh_fails("not-a-token")
    refresh_fails(issued.refresh_token)


def test_inactive_user_cannot_refresh() -> None:
    user = make_user()
    issued = sign_in(user)
    user.is_active = False
    user.save()

    refresh_fails(issued.refresh_token)
    assert authenticate(issued.access_token) is None


def test_sign_out_revokes_the_session() -> None:
    issued = sign_in(make_user())

    sessions.revoke_session(issued.refresh_token)

    refresh_fails(issued.refresh_token)
    assert authenticate(issued.access_token) is None


def test_expired_and_tampered_access_tokens_are_refused(settings) -> None:
    user = make_user()
    settings.ACCESS_TOKEN_TTL_SECONDS = -1
    expired = sign_in(user).access_token
    settings.ACCESS_TOKEN_TTL_SECONDS = 900
    valid = sign_in(user).access_token
    header, payload, signature = valid.split(".")
    tampered = f"{header}.{payload}.{signature[::-1]}"
    unsigned = jwt.encode({"sub": str(user.pk)}, key="", algorithm="none")

    for token in (expired, tampered, unsigned, ""):
        assert authenticate(token) is None


def test_token_types_are_not_interchangeable() -> None:
    issued = sign_in(make_user())
    ws_token = sessions.issue_websocket_token(issued.session)

    # A WebSocket token never works as a bearer token, and vice versa where not allowed.
    assert authenticate(ws_token) is None
    assert authenticate(issued.access_token, sessions.WEBSOCKET) is None
    assert authenticate(ws_token, sessions.WEBSOCKET) is not None


def test_websocket_token_lives_about_a_minute(settings) -> None:
    issued = sign_in(make_user())

    claims = jwt.decode(
        sessions.issue_websocket_token(issued.session),
        settings.JWT_SIGNING_KEY,
        algorithms=["HS256"],
        audience="taskbeavo-api",
    )

    assert claims["typ"] == "ws"
    assert claims["exp"] - claims["iat"] == 60


# --- Log out of all devices, "This wasn't me" and the new-device email (BEAV-76) -----


def test_sign_out_everywhere_revokes_every_session_of_the_user_only(
    django_capture_on_commit_callbacks,
) -> None:
    user, other = make_user("ana"), make_user("marko")
    phone, laptop = sign_in(user), sign_in(user, Session.Platform.WEB)
    others = sign_in(other)
    PushToken.objects.create(user=user, session=phone.session, token="ExponentPushToken[a]")

    with django_capture_on_commit_callbacks(execute=True):
        assert sessions.revoke_all_sessions(user) == 2

    for issued in (phone, laptop):
        refresh_fails(issued.refresh_token)
        assert authenticate(issued.access_token) is None
    assert authenticate(others.access_token) is not None
    # session_revoked reached notifications: the device's push token is gone.
    assert not PushToken.objects.exists()


def test_every_revocation_sends_session_revoked(
    settings, django_capture_on_commit_callbacks
) -> None:
    settings.REFRESH_TOKEN_REUSE_GRACE_SECONDS = 0
    user = make_user()
    announced: list[list[Any]] = []

    def receiver(sender: Any, session_ids: list[Any], **kwargs: Any) -> None:
        announced.append(sorted(session_ids))

    sessions.session_revoked.connect(receiver)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            out = sign_in(user)
            sessions.revoke_session(out.refresh_token)
            reused = sign_in(user)
            sessions.refresh_session(reused.refresh_token)
            RefreshToken.objects.filter(used_at__isnull=False).update(
                used_at=timezone.now() - dt.timedelta(seconds=1)
            )
            with pytest.raises(ApiError):
                sessions.refresh_session(reused.refresh_token)
            everywhere = sign_in(user)
            sessions.revoke_all_sessions(user)
            linked = sign_in(user)
            sessions.revoke_all_from_link(sessions.sign_out_link_token(linked.session))
            by_support = sign_in(user)
            sessions.revoke_sessions([by_support.session.pk, linked.session.pk])
    finally:
        sessions.session_revoked.disconnect(receiver)

    assert announced == [
        [out.session.pk],
        [reused.session.pk],
        [everywhere.session.pk],
        [linked.session.pk],
        # Support's revocation skips the session that was already revoked.
        [by_support.session.pk],
    ]


def test_support_revokes_sessions_with_the_admin_action(
    client, django_capture_on_commit_callbacks
) -> None:
    client.force_login(make_user("support", is_staff=True, is_superuser=True))
    admin_client = client
    user = make_user()
    issued = sign_in(user)

    with django_capture_on_commit_callbacks(execute=True):
        response = admin_client.post(
            "/admin/accounts/session/",
            {"action": "revoke", "_selected_action": [str(issued.session.pk)]},
        )

    assert response.status_code == 302
    refresh_fails(issued.refresh_token)
    # The change form can't set revoked_at by hand, which would skip the announcement.
    form = admin_client.get(f"/admin/accounts/session/{issued.session.pk}/change/")
    assert b'name="revoked_at' not in form.content


def test_sign_out_link_logs_the_account_out_everywhere() -> None:
    user = make_user()
    first, second = sign_in(user), sign_in(user, Session.Platform.WEB)

    assert sessions.revoke_all_from_link(sessions.sign_out_link_token(second.session)) == 2

    assert not Session.objects.filter(revoked_at__isnull=True).exists()
    assert authenticate(first.access_token) is None


@pytest.mark.parametrize("token", ["", "garbage", "a:b:c"])
def test_forged_sign_out_links_are_refused(token: str) -> None:
    sign_in(make_user())

    with pytest.raises(ApiError) as excinfo:
        sessions.revoke_all_from_link(token)

    assert excinfo.value.code is ErrorCode.SIGN_OUT_LINK_EXPIRED
    assert Session.objects.get().revoked_at is None


def test_old_sign_out_links_are_refused(settings) -> None:
    issued = sign_in(make_user())
    token = sessions.sign_out_link_token(issued.session)
    settings.SIGN_OUT_LINK_MAX_AGE_SECONDS = -1

    with pytest.raises(ApiError) as excinfo:
        sessions.revoke_all_from_link(token)

    assert excinfo.value.code is ErrorCode.SIGN_OUT_LINK_EXPIRED


def start(
    user: Any, platform: str, name: str, ip: str = "", device_token: str | None = None
) -> sessions.IssuedTokens:
    return sessions.start_session(user, platform, name, ip, device_token)


def test_the_first_session_of_an_account_sends_no_notice(
    django_capture_on_commit_callbacks,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        start(make_user(locale="en"), "ios", "iPhone")

    assert mail.outbox == []
    assert not Outbox.objects.exists()


def test_a_device_with_its_token_is_known(django_capture_on_commit_callbacks) -> None:
    user = make_user(locale="en")
    first = start(user, "web", "Firefox on Mac")
    assert first.device_token is not None

    with django_capture_on_commit_callbacks(execute=True):
        again = start(user, "web", "Firefox on Mac", device_token=first.device_token)
        sessions.revoke_session(again.refresh_token)
        start(user, "web", "Firefox on Mac", device_token=first.device_token)

    assert mail.outbox == []
    assert again.device_token == first.device_token


@pytest.mark.parametrize("how", ["everywhere", "this-wasnt-me"])
def test_logging_out_everywhere_forgets_every_device(
    django_capture_on_commit_callbacks, how: str
) -> None:
    """A stranger's device token stops counting as known once the account is secured."""
    user = make_user(locale="en")
    start(user, "web", "Firefox on Mac")
    stranger = start(user, "ios", "iPhone")
    session = Session.objects.get(user=user, platform="ios")

    if how == "everywhere":
        sessions.revoke_all_sessions(user)
    else:
        sessions.revoke_all_from_link(sessions.sign_out_link_token(session))
    with django_capture_on_commit_callbacks(execute=True):
        start(user, "ios", "iPhone", device_token=stranger.device_token)

    assert [m.subject for m in mail.outbox] == ["New login to TaskBeavo from iPhone"]


def test_the_same_name_and_platform_without_the_token_is_a_new_device(
    django_capture_on_commit_callbacks,
) -> None:
    """The client chooses its platform and name: copying them doesn't hide a sign-in."""
    user = make_user(locale="en")
    start(user, "ios", "iPhone")

    with django_capture_on_commit_callbacks(execute=True):
        start(user, "ios", "iPhone")

    assert mail.outbox[0].subject == "New login to TaskBeavo from iPhone"


@pytest.mark.parametrize("token", ["", "short", "x" * 43 + "!", "a" * 200])
def test_a_malformed_device_token_is_replaced(token: str) -> None:
    issued = start(make_user(), "web", "Firefox on Mac", device_token=token)

    assert issued.device_token != token
    assert issued.device_token is not None and len(issued.device_token) == 43


def test_another_accounts_device_token_is_a_new_device(
    django_capture_on_commit_callbacks,
) -> None:
    ana, marko = make_user("ana", locale="en"), make_user("marko", locale="en")
    marko_phone = start(marko, "ios", "iPhone")
    start(ana, "web", "Firefox on Mac")

    with django_capture_on_commit_callbacks(execute=True):
        start(ana, "ios", "iPhone", device_token=marko_phone.device_token)

    assert [m.to for m in mail.outbox] == [[ana.email]]


def test_refresh_hands_out_no_device_token() -> None:
    issued = sign_in(make_user())

    assert sessions.refresh_session(issued.refresh_token).device_token is None


def test_a_new_device_emails_the_owner_in_their_language(
    django_capture_on_commit_callbacks, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sessions.geo, "coarse_location", lambda ip, locale: "München, Deutschland")
    user = make_user(locale="de", time_zone="Europe/Berlin")
    start(user, "web", "Firefox on Mac", "203.0.113.9")

    with django_capture_on_commit_callbacks(execute=True):
        new = start(user, "ios", "iPhone", "203.0.113.9")

    [email] = mail.outbox
    assert email.to == [user.email]
    assert email.subject == "Neue Anmeldung bei TaskBeavo von iPhone, München, Deutschland"
    assert "203.0.113.9" not in email.body
    link = re.search(r"https?://\S+/de/this-wasnt-me\?token=(\S+)", email.body)
    assert link is not None
    # The link logs out everywhere, the new session included.
    sessions.revoke_all_from_link(unquote(link.group(1)))
    assert authenticate(new.access_token) is None
    assert Session.objects.get(pk=new.session.pk).location == "München, Deutschland"


def test_the_notice_names_where_the_sign_in_came_from(
    django_capture_on_commit_callbacks, monkeypatch: pytest.MonkeyPatch
) -> None:
    places = {"203.0.113.1": "Dubrovnik, Croatia", "198.51.100.1": "Lagos, Nigeria"}
    monkeypatch.setattr(sessions.geo, "coarse_location", lambda ip, locale: places[ip])
    user = make_user(locale="en")
    start(user, "ios", "iPhone", "203.0.113.1")

    with django_capture_on_commit_callbacks(execute=True):
        start(user, "ios", "iPhone", "198.51.100.1")

    assert mail.outbox[0].subject == "New login to TaskBeavo from iPhone, Lagos, Nigeria"


def test_a_device_name_that_isnt_an_apps_shows_the_platform(
    django_capture_on_commit_callbacks,
) -> None:
    """The client chooses the name: a stranger can't write their own text into the email."""
    user = make_user(locale="en")
    start(user, "ios", "iPhone")

    with django_capture_on_commit_callbacks(execute=True):
        start(user, "web", "Call support on +385 91 000 0000")

    [email] = mail.outbox
    assert email.subject == "New login to TaskBeavo from Web browser"
    assert "+385" not in email.body


def test_the_new_device_email_goes_once_per_outbox_row(
    django_capture_on_commit_callbacks,
) -> None:
    user = make_user(locale="en")
    start(user, "ios", "iPhone")
    with django_capture_on_commit_callbacks(execute=True):
        start(user, "android", "Android")
    row = Outbox.objects.get(topic=sessions.NEW_DEVICE_TOPIC)

    assert row.payload == {}
    assert outbox_key(row, "email") == f"outbox:{row.pk}:email:v1"


def test_the_coarse_location_comes_from_the_geoip_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reader:
        def get(self, address: str) -> dict[str, Any]:
            assert address == "81.2.69.160"
            return {
                "city": {"names": {"en": "Munich", "de": "München"}},
                "country": {"names": {"en": "Germany", "de": "Deutschland"}},
            }

    monkeypatch.setattr(geo, "_reader", lambda path: Reader())

    assert geo.coarse_location("81.2.69.160", "de") == "München, Deutschland"
    # No Croatian names in GeoLite2: English.
    assert geo.coarse_location("81.2.69.160", "hr") == "Munich, Germany"
    # Private and invalid addresses have no location.
    assert geo.coarse_location("10.0.0.1", "en") == ""
    assert geo.coarse_location("not-an-ip", "en") == ""


def test_without_a_geoip_database_the_location_is_empty(settings) -> None:
    settings.GEOIP_CITY_DATABASE = ""

    assert geo.coarse_location("81.2.69.160", "en") == ""


def test_ended_sessions_are_deleted_six_months_after_they_end(settings) -> None:
    from accounts.tasks import purge_ended_sessions
    from core.models import Outbox
    from notifications.models import PushToken

    settings.SESSION_RETENTION_DAYS = 183
    settings.REFRESH_TOKEN_TTL_DAYS = 30
    now = timezone.now()
    user = make_user(locale="en")
    start(user, "web", "Firefox on Mac")

    def session(**fields: Any) -> Session:
        issued = start(user, "ios", "iPhone")
        Session.objects.filter(pk=issued.session.pk).update(**fields)
        return Session.objects.get(pk=issued.session.pk)

    old_revoked = session(revoked_at=now - dt.timedelta(days=184), location="Lagos, Nigeria")
    recent_revoked = session(revoked_at=now - dt.timedelta(days=182))
    old_expired = session(last_used_at=now - dt.timedelta(days=30 + 184))
    still_in_retention = session(last_used_at=now - dt.timedelta(days=30 + 182))
    # Live, but idle for a while: its refresh token may still be valid.
    live = session(last_used_at=now - dt.timedelta(days=29))
    PushToken.objects.create(
        user=user, session=old_expired, token="ExponentPushToken[old]", platform="ios"
    )

    assert purge_ended_sessions() == 2

    remaining = set(Session.objects.values_list("pk", flat=True))
    assert old_revoked.pk not in remaining and old_expired.pk not in remaining
    assert {recent_revoked.pk, still_in_retention.pk, live.pk} <= remaining
    assert not PushToken.objects.filter(token="ExponentPushToken[old]").exists()
    # A new-device email still pending for a deleted session is skipped, not an error.
    from accounts.outbox_handlers import email_new_device

    row = Outbox.objects.get(topic=sessions.NEW_DEVICE_TOPIC, aggregate_id=str(old_revoked.pk))
    email_new_device(row)
    assert all("Lagos" not in m.subject for m in mail.outbox)


def test_a_device_token_unused_for_six_months_is_unknown_and_replaced(
    settings, django_capture_on_commit_callbacks
) -> None:
    settings.DEVICE_TOKEN_MAX_AGE_DAYS = 183
    user = make_user(locale="en")
    old = start(user, "ios", "iPhone")
    Session.objects.update(created_at=timezone.now() - dt.timedelta(days=184))
    start(user, "web", "Firefox on Mac", device_token="w" * 43)  # another device, today

    with django_capture_on_commit_callbacks(execute=True):
        again = start(user, "ios", "iPhone", device_token=old.device_token)

    assert [m.subject for m in mail.outbox] == ["New login to TaskBeavo from iPhone"]
    assert again.device_token != old.device_token


def test_each_sign_in_renews_the_device_tokens_six_months(
    settings, django_capture_on_commit_callbacks
) -> None:
    settings.DEVICE_TOKEN_MAX_AGE_DAYS = 183
    user = make_user(locale="en")
    first = start(user, "ios", "iPhone")
    Session.objects.update(created_at=timezone.now() - dt.timedelta(days=150))
    start(user, "ios", "iPhone", device_token=first.device_token)  # renews: a session today
    Session.objects.filter(created_at__lt=timezone.now() - dt.timedelta(days=1)).update(
        created_at=timezone.now() - dt.timedelta(days=300)
    )

    with django_capture_on_commit_callbacks(execute=True):
        again = start(user, "ios", "iPhone", device_token=first.device_token)

    assert mail.outbox == []
    assert again.device_token == first.device_token


def test_a_sign_in_after_every_session_was_purged_is_not_the_accounts_first(
    django_capture_on_commit_callbacks,
) -> None:
    user = make_user(locale="en")
    start(user, "web", "Firefox on Mac")
    Session.objects.all().delete()  # as purge_ended_sessions does after 6 months

    with django_capture_on_commit_callbacks(execute=True):
        start(user, "ios", "iPhone")

    assert [m.subject for m in mail.outbox] == ["New login to TaskBeavo from iPhone"]


def test_only_staff_who_may_change_sessions_can_revoke_them(client) -> None:
    from django.contrib.auth.models import Permission

    viewer = make_user("viewer", is_staff=True)
    viewer.user_permissions.add(
        Permission.objects.get(codename="view_session", content_type__app_label="accounts")
    )
    client.force_login(viewer)
    issued = sign_in(make_user())

    client.post(
        "/admin/accounts/session/",
        {"action": "revoke", "_selected_action": [str(issued.session.pk)]},
    )

    assert Session.objects.get(pk=issued.session.pk).revoked_at is None
