"""The helper's "Request payment" (BEAV-116; PLAN.md, "The adapted flow keeps Airtasker's
release model"): photos and a note, the registered business's invoice or JIR, the poster
told by push, email and a live event, and no release before the request. No money moves
(hard rule a); the move is locked, audited and written to the outbox (hard rule c); the
photos, note and invoice are the booking's two sides' only (hard rule e)."""

import datetime as dt
import threading

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone

from accounts.models import User
from bookings import payment_request, state_machine
from bookings.models import Booking, BookingEvent, PaymentRequest
from bookings.tests.fakes import FakeGateway
from bookings.tests.test_checkout import (  # noqa: F401 (fixtures)
    _fresh_rate_limits,
    legal_documents,
    publish,
    webhook_secret,
)
from bookings.tests.test_poster_cancel import code, helper, paid, poster, token
from core.errors import ApiError, ErrorCode
from core.models import ActorKind, Outbox
from helpers.models import HelperProfile
from notifications.models import Delivery
from offers.tests.factories import make_helper
from tasks.tests.factories import ready_photo
from uploads.models import Purpose, Upload

pytestmark = pytest.mark.django_db

S = Booking.Status
H = dt.timedelta(hours=1)
JIR = "6B8A3C2E-1F4D-4A7B-9C0E-5D2F8A1B3C4D"


def started(booking: Booking) -> Booking:
    """The job started an hour ago."""
    Booking.objects.filter(pk=booking.pk).update(slot_start=timezone.now() - H)
    booking.refresh_from_db()
    return booking


def evidence(user: User, count: int = 1) -> list[Upload]:
    return [ready_photo(user, Purpose.EVIDENCE_PHOTO) for _ in range(count)]


def request(booking: Booking, **fields: object) -> Booking:
    return payment_request.request_payment(helper(booking), booking.pk, **fields)  # type: ignore[arg-type]


# --- The move. ---


def test_the_helper_requests_payment_with_photos_and_a_note(
    client: Client, gateway: FakeGateway
) -> None:
    booking = started(paid(client))
    photos = evidence(helper(booking), 3)
    charges_before = len(gateway.charges)

    requested = request(
        booking,
        note="  All delivered. Mama says hvala!  ",
        photo_ids=[photos[2].pk, photos[0].pk, photos[1].pk],
    )

    assert requested.status == S.PAYMENT_REQUESTED
    assert requested.payment_requested_at is not None
    stored = PaymentRequest.objects.get(booking=booking)
    assert stored.note == "All delivered. Mama says hvala!"
    assert [p.upload_id for p in stored.photos.all()] == [photos[2].pk, photos[0].pk, photos[1].pk]
    assert stored.invoice_upload is None and stored.invoice_jir == ""
    for photo in photos:
        photo.refresh_from_db()
        assert photo.attached_at is not None
    # Hard rule a: the request moves no money.
    assert len(gateway.charges) == charges_before
    assert gateway.refunds == [] and gateway.cancels == []


def test_the_move_is_audited_without_the_note_and_written_to_the_outbox(
    client: Client, gateway: FakeGateway
) -> None:
    booking = started(paid(client))

    request(
        booking, note="Key is under the mat", photo_ids=[p.pk for p in evidence(helper(booking), 2)]
    )

    event = BookingEvent.objects.get(booking=booking, type="payment_requested")
    assert event.actor_kind == ActorKind.HELPER
    assert event.actor_id == booking.helper_id
    assert event.data == {
        "from": "FUNDS_HELD",
        "to": "PAYMENT_REQUESTED",
        "photos": 2,
        "commented": True,
        "invoice": None,
    }
    assert "mat" not in str(event.data)
    topics = set(
        Outbox.objects.filter(aggregate_id=str(booking.pk)).values_list("topic", flat=True)
    )
    assert {state_machine.UPDATED_TOPIC, payment_request.REQUESTED_TOPIC} <= topics


def test_photos_and_note_are_optional(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))

    assert request(booking).status == S.PAYMENT_REQUESTED
    stored = PaymentRequest.objects.get(booking=booking)
    assert stored.note == "" and not stored.photos.exists()


def test_not_before_the_job_started(client: Client, gateway: FakeGateway) -> None:
    booking = paid(client)
    Booking.objects.filter(pk=booking.pk).update(slot_start=timezone.now() + H)

    with pytest.raises(ApiError) as exc:
        request(booking)

    assert code(exc) == ErrorCode.PAYMENT_REQUEST_TOO_EARLY.value
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert not PaymentRequest.objects.exists()


def test_only_the_bookings_helper_can_request(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))

    for user in (poster(booking), make_helper("stranger")):
        with pytest.raises(ApiError) as exc:
            payment_request.request_payment(user, booking.pk)
        assert code(exc) == ErrorCode.BOOKING_NOT_FOUND.value


@pytest.mark.parametrize(
    "status", [S.AWAITING_PAYMENT, S.CANCEL_REQUESTED, S.DISPUTED, S.RELEASED, S.REFUNDED]
)
def test_only_while_the_money_is_held(
    client: Client, gateway: FakeGateway, status: Booking.Status
) -> None:
    booking = started(paid(client))
    Booking.objects.filter(pk=booking.pk).update(status=status)

    with pytest.raises(ApiError) as exc:
        request(booking)

    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


def test_payment_is_requested_once(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))
    request(booking)

    with pytest.raises(ApiError) as exc:
        request(booking, note="again")

    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value
    assert PaymentRequest.objects.get(booking=booking).note == ""


def test_a_reopened_tasks_booking_cant_request(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))
    Booking.objects.filter(pk=booking.pk).update(task_reopened_at=timezone.now())

    with pytest.raises(ApiError) as exc:
        request(booking)

    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


@pytest.mark.django_db(transaction=True)
def test_two_parallel_requests_make_one(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))
    results: list[str] = []
    barrier = threading.Barrier(2)

    def tap() -> None:
        barrier.wait()
        try:
            request(booking)
            results.append("ok")
        except ApiError as error:
            results.append(error.extensions["code"])
        finally:
            connection.close()

    threads = [threading.Thread(target=tap) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["BOOKING_ACTION_NOT_ALLOWED", "ok"]
    assert PaymentRequest.objects.filter(booking=booking).count() == 1
    assert BookingEvent.objects.filter(booking=booking, type="payment_requested").count() == 1


# --- What is accepted. ---


def test_the_note_is_capped(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))

    with pytest.raises(ApiError) as exc:
        request(booking, note="x" * (payment_request.NOTE_MAX_LENGTH + 1))

    assert code(exc) == ErrorCode.INVALID_PAYMENT_REQUEST.value


def test_at_most_six_photos(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))
    photos = evidence(helper(booking), payment_request.MAX_PHOTOS + 1)

    with pytest.raises(ApiError) as exc:
        request(booking, photo_ids=[p.pk for p in photos])

    assert code(exc) == ErrorCode.INVALID_PAYMENT_REQUEST.value


def test_photos_must_be_the_helpers_own_evidence(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))
    someone_elses = evidence(poster(booking))[0]
    task_photo = ready_photo(helper(booking), Purpose.TASK_PHOTO)

    for photo in (someone_elses, task_photo):
        with pytest.raises(ApiError) as exc:
            request(booking, photo_ids=[photo.pk])
        assert code(exc) == ErrorCode.UPLOAD_NOT_FOUND.value
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD


# --- The registered business's invoice. ---


def test_a_trader_must_add_the_invoice(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client, trader=True))

    with pytest.raises(ApiError) as exc:
        request(booking, note="Done")

    assert code(exc) == ErrorCode.INVOICE_REQUIRED.value
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD


def test_a_trader_can_give_the_jir(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client, trader=True))

    request(booking, invoice_jir=f" {JIR} ")

    stored = PaymentRequest.objects.get(booking=booking)
    assert stored.invoice_jir == JIR.lower()
    event = BookingEvent.objects.get(booking=booking, type="payment_requested")
    assert event.data["invoice"] == "JIR"
    assert JIR.lower() not in str(event.data)


def test_a_trader_can_upload_the_invoice(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client, trader=True))
    invoice = ready_photo(helper(booking), Purpose.INVOICE)

    request(booking, invoice_upload_id=invoice.pk)

    assert PaymentRequest.objects.get(booking=booking).invoice_upload_id == invoice.pk
    invoice.refresh_from_db()
    assert invoice.attached_at is not None


@pytest.mark.parametrize(
    "jir", ["", "not-a-jir", "6b8a3c2e1f4d4a7b9c0e5d2f8a1b3c4d", JIR + "0", "12345678901"]
)
def test_the_jir_format_is_checked(client: Client, gateway: FakeGateway, jir: str) -> None:
    booking = started(paid(client, trader=True))

    with pytest.raises(ApiError) as exc:
        request(booking, invoice_jir=jir or " ")

    assert code(exc) in (
        ErrorCode.INVALID_PAYMENT_REQUEST.value,
        ErrorCode.INVOICE_REQUIRED.value,
    )
    assert not PaymentRequest.objects.exists()


def test_the_invoice_must_be_the_helpers_own_invoice_upload(
    client: Client, gateway: FakeGateway
) -> None:
    booking = started(paid(client, trader=True))
    photo = evidence(helper(booking))[0]

    with pytest.raises(ApiError) as exc:
        request(booking, invoice_upload_id=photo.pk)

    assert code(exc) == ErrorCode.UPLOAD_NOT_FOUND.value


def test_a_private_helper_skips_the_invoice(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))

    with pytest.raises(ApiError) as exc:
        request(booking, invoice_jir=JIR)

    assert code(exc) == ErrorCode.INVALID_PAYMENT_REQUEST.value


def test_a_helper_who_registered_since_checkout_may_add_the_invoice(
    client: Client, gateway: FakeGateway
) -> None:
    booking = started(paid(client))
    HelperProfile.objects.filter(user_id=booking.helper_id).update(
        declared_status=HelperProfile.Status.BUSINESS
    )

    request(booking, invoice_jir=JIR)

    assert PaymentRequest.objects.get(booking=booking).invoice_jir == JIR.lower()


# --- Telling the poster, and no release before the request. ---


def test_the_poster_is_told_by_push_and_email(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))
    request(booking, note="Done")

    publish(payment_request.REQUESTED_TOPIC, booking)

    told = Delivery.objects.filter(kind="booking.payment_requested")
    assert set(told.values_list("user_id", flat=True)) == {booking.poster_id}
    assert set(told.values_list("channel", flat=True)) >= {"email"}
    assert not Delivery.objects.filter(
        user_id=booking.helper_id, kind="booking.payment_requested"
    ).exists()


def test_the_poster_cant_release_before_the_request(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))

    with pytest.raises(ApiError) as exc:
        state_machine.transition(
            booking.pk, S.RELEASED, actor_kind=ActorKind.POSTER, actor=poster(booking)
        )

    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value
    request(booking)
    released = state_machine.transition(
        booking.pk, S.RELEASED, actor_kind=ActorKind.POSTER, actor=poster(booking)
    )
    assert released.status == S.RELEASED


def test_a_dispute_holds_the_photos_and_invoice(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client, trader=True))
    photo = evidence(helper(booking))[0]
    invoice = ready_photo(helper(booking), Purpose.INVOICE)
    request(booking, photo_ids=[photo.pk], invoice_upload_id=invoice.pk)

    state_machine.transition(
        booking.pk, S.DISPUTED, actor_kind=ActorKind.POSTER, actor=poster(booking)
    )

    for upload in (photo, invoice):
        upload.refresh_from_db()
        assert upload.legal_hold


def test_closing_the_booking_starts_the_photos_retention(
    client: Client, gateway: FakeGateway
) -> None:
    booking = started(paid(client))
    photo = evidence(helper(booking))[0]
    request(booking, photo_ids=[photo.pk])
    Booking.objects.filter(pk=booking.pk).update(status=S.PAID_OUT)

    state_machine.transition(booking.pk, S.CLOSED, actor_kind=ActorKind.SYSTEM)

    photo.refresh_from_db()
    assert photo.delete_after is not None


# --- Over the API. ---

FIELDS = """
  id status
  canRequestPayment invoiceRequired
  paymentRequest {
    id requestedAt note invoiceJir invoice { id url }
    photos { id url width height }
  }
"""
BOOKING = "query($id: ID!) { booking(id: $id) { " + FIELDS + " } }"
REQUEST_PAYMENT = (
    "mutation($input: RequestPaymentInput!) { requestPayment(input: $input) { " + FIELDS + " } }"
)


def test_request_payment_over_the_api(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client, trader=True))
    photo = evidence(helper(booking))[0]
    variables = {
        "input": {
            "bookingId": str(booking.pk),
            "note": "All delivered",
            "photoIds": [str(photo.pk)],
            "invoiceJir": JIR,
        }
    }
    before = gql_booking(client, booking, helper(booking))
    assert before["canRequestPayment"] is True
    assert before["invoiceRequired"] is True
    assert before["paymentRequest"] is None

    from accounts.tests.test_schema import error_codes, gql

    assert error_codes(gql(client, REQUEST_PAYMENT, variables)) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, REQUEST_PAYMENT, variables, token=token(poster(booking)))) == [
        "BOOKING_NOT_FOUND"
    ]
    body = gql(client, REQUEST_PAYMENT, variables, token=token(helper(booking)))

    result = body["data"]["requestPayment"]
    assert result["status"] == "PAYMENT_REQUESTED"
    assert result["canRequestPayment"] is False
    shown = result["paymentRequest"]
    assert shown["id"] == str(booking.pk)
    assert shown["note"] == "All delivered"
    assert shown["invoiceJir"] == JIR.lower()
    assert [p["id"] for p in shown["photos"]] == [str(photo.pk)]
    # The clean copy, never the original with its metadata.
    assert "display" in shown["photos"][0]["url"]
    assert "original" not in shown["photos"][0]["url"]

    seen = gql_booking(client, booking, poster(booking))
    assert seen["paymentRequest"]["note"] == "All delivered"
    assert seen["paymentRequest"]["photos"][0]["id"] == str(photo.pk)
    assert seen["canRequestPayment"] is False and seen["invoiceRequired"] is False


def test_a_private_helpers_booking_needs_no_invoice(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))

    shown = gql_booking(client, booking, helper(booking))

    assert shown["canRequestPayment"] is True
    assert shown["invoiceRequired"] is False


def test_the_poster_never_gets_the_request_button(client: Client, gateway: FakeGateway) -> None:
    booking = started(paid(client))

    assert gql_booking(client, booking, poster(booking))["canRequestPayment"] is False


def test_the_button_waits_for_the_start(client: Client, gateway: FakeGateway) -> None:
    booking = paid(client)
    Booking.objects.filter(pk=booking.pk).update(slot_start=timezone.now() + H)

    assert gql_booking(client, booking, helper(booking))["canRequestPayment"] is False


def test_nobody_else_sees_the_request(client: Client, gateway: FakeGateway) -> None:
    from accounts.tests.test_schema import error_codes, gql

    booking = started(paid(client))
    request(booking, note="All delivered", photo_ids=[p.pk for p in evidence(helper(booking))])

    body = gql(client, BOOKING, {"id": str(booking.pk)}, token=token(make_helper("stranger")))

    assert error_codes(body) == ["BOOKING_NOT_FOUND"]
    assert "All delivered" not in str(body)


def gql_booking(client: Client, booking: Booking, user: User) -> dict:
    from accounts.tests.test_schema import gql

    body = gql(client, BOOKING, {"id": str(booking.pk)}, token=token(user))
    assert "errors" not in body, body
    return body["data"]["booking"]
