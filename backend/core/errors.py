"""Error codes the API returns in `errors[].extensions.code` (hard rule i).

The API never returns user-facing text: clients translate `errors.<CODE>` from
packages/i18n. Every code is a member here, so it is documented in schema.graphql
and the i18n missing-key check can require a translation for it. A member's name
is its wire value; use `ErrorCode.X.name` for the string.
"""

from collections.abc import Mapping
from enum import Enum

import strawberry
from graphql import GraphQLError


@strawberry.enum(
    description=(
        "Codes returned in `errors[].extensions.code`. Clients show the translation "
        "`errors.<CODE>` from packages/i18n; the error message itself is for developers only."
    )
)
class ErrorCode(Enum):
    RATE_LIMITED = strawberry.enum_value(
        "RATE_LIMITED",
        description=(
            "Too many calls of this mutation from this client. `extensions.retryAfterSeconds`, "
            "when present, says how long to wait (up to a day for SMS codes); otherwise retry "
            "after a minute."
        ),
    )
    MUTATIONS_REQUIRE_HTTP = strawberry.enum_value(
        "MUTATIONS_REQUIRE_HTTP",
        description="A mutation was sent over the WebSocket; send mutations over HTTP.",
    )
    UNAUTHENTICATED = strawberry.enum_value(
        "UNAUTHENTICATED",
        description="The operation needs a signed-in user and none was authenticated.",
    )
    INVALID_EMAIL = strawberry.enum_value(
        "INVALID_EMAIL", description="The email address isn't a valid address."
    )
    INVALID_WITHDRAWAL_DETAILS = strawberry.enum_value(
        "INVALID_WITHDRAWAL_DETAILS",
        description="The name or email address on a withdrawal statement is empty or invalid.",
    )
    INVALID_CODE = strawberry.enum_value(
        "INVALID_CODE",
        description=(
            "The emailed or SMS code is wrong, expired, already used or burnt after too many "
            "wrong tries; request a new one."
        ),
    )
    SIGNUP_TICKET_EXPIRED = strawberry.enum_value(
        "SIGNUP_TICKET_EXPIRED",
        description=(
            "The account-details ticket is invalid, expired or its account already exists; "
            "sign in again with a new code."
        ),
    )
    INVALID_PROVIDER_TOKEN = strawberry.enum_value(
        "INVALID_PROVIDER_TOKEN",
        description=(
            "The Google or Apple sign-in couldn't be verified (expired, for another app, or "
            "its nonce is wrong or used); start the sign-in again with a new nonce."
        ),
    )
    PROVIDER_UNAVAILABLE = strawberry.enum_value(
        "PROVIDER_UNAVAILABLE",
        description=(
            "Google or Apple couldn't be reached to check the sign-in; try again in a few "
            "minutes, or sign in with an email code."
        ),
    )
    PROVIDER_EMAIL_MISSING = strawberry.enum_value(
        "PROVIDER_EMAIL_MISSING",
        description=(
            "Google or Apple didn't share a verified email address, which a new account "
            "needs; allow sharing the email, or sign in with an email code."
        ),
    )
    UNDERAGE = strawberry.enum_value(
        "UNDERAGE", description="The person didn't confirm they are 18 or older."
    )
    INVALID_ACCOUNT_DETAILS = strawberry.enum_value(
        "INVALID_ACCOUNT_DETAILS",
        description="A name, country or time zone in the account details is missing or invalid.",
    )
    TERMS_NOT_ACCEPTED = strawberry.enum_value(
        "TERMS_NOT_ACCEPTED",
        description=(
            "The account details or acceptCurrentTerms didn't accept the current terms and "
            "conditions: the version is missing or isn't `currentLegalDocument(kind: TERMS)`'s "
            "version (they may have just changed). Fetch the current version, show it, and "
            "ask again."
        ),
    )
    SESSION_EXPIRED = strawberry.enum_value(
        "SESSION_EXPIRED",
        description=(
            "The refresh token is unknown, expired, reused or its session was revoked; "
            "sign in again."
        ),
    )
    LOCKED = strawberry.enum_value(
        "LOCKED",
        description=(
            "Too many wrong sign-in codes for this email address: sign-in is locked for 30 "
            "minutes. `extensions.retryAfterSeconds` says how long is left."
        ),
    )
    SIGN_OUT_LINK_EXPIRED = strawberry.enum_value(
        "SIGN_OUT_LINK_EXPIRED",
        description=(
            'The "This wasn\'t me" link is invalid or too old; sign in and use "Log out of '
            'all devices" in settings instead.'
        ),
    )
    REQUEST_REJECTED = strawberry.enum_value(
        "REQUEST_REJECTED",
        description=(
            "The web app's /api routes refused a request that didn't come from its own pages "
            "(cross-site request forgery check); reload the page and try again."
        ),
    )
    INVALID_PUSH_TOKEN = strawberry.enum_value(
        "INVALID_PUSH_TOKEN",
        description=(
            "The push token isn't an Expo push token, or is still registered to another "
            "signed-in account."
        ),
    )
    NOTIFICATION_PREFERENCE_LOCKED = strawberry.enum_value(
        "NOTIFICATION_PREFERENCE_LOCKED",
        description="Booking and payment notifications are part of the service and stay on.",
    )
    UPLOAD_TYPE_NOT_ALLOWED = strawberry.enum_value(
        "UPLOAD_TYPE_NOT_ALLOWED",
        description="Photos must be JPEG, PNG or WebP.",
    )
    UPLOAD_TOO_LARGE = strawberry.enum_value(
        "UPLOAD_TOO_LARGE",
        description="The photo is larger than the upload limit, in bytes or in pixels.",
    )
    TOO_MANY_PENDING_UPLOADS = strawberry.enum_value(
        "TOO_MANY_PENDING_UPLOADS",
        description=(
            "Too many uploads not used yet (started, being processed, or ready and not "
            "attached to anything); use them or wait for them to expire."
        ),
    )
    UPLOAD_NOT_FOUND = strawberry.enum_value(
        "UPLOAD_NOT_FOUND",
        description=(
            "No such upload of the signed-in user, or it can't be used here (not ready, "
            "another purpose, or already attached)."
        ),
    )
    UPLOAD_NOT_RECEIVED = strawberry.enum_value(
        "UPLOAD_NOT_RECEIVED",
        description="The file hasn't arrived in storage; PUT it to the upload URL first.",
    )
    EVIDENCE_NEEDS_APP = strawberry.enum_value(
        "EVIDENCE_NEEDS_APP",
        description="Evidence photos are taken in the TaskBeavo app, not uploaded from the web.",
    )
    UPLOAD_INVALID_IMAGE = strawberry.enum_value(
        "UPLOAD_INVALID_IMAGE",
        description="The file isn't a readable image of the type it was uploaded as.",
    )
    INVALID_PHONE_NUMBER = strawberry.enum_value(
        "INVALID_PHONE_NUMBER",
        description="The phone number isn't a valid mobile number.",
    )
    PHONE_COUNTRY_NOT_SUPPORTED = strawberry.enum_value(
        "PHONE_COUNTRY_NOT_SUPPORTED",
        description=(
            "TaskBeavo can't send SMS to mobile numbers of this country yet (no sender for "
            "it, e.g. +1 numbers, or switched off); use another mobile number."
        ),
    )
    PHONE_NUMBER_IN_USE = strawberry.enum_value(
        "PHONE_NUMBER_IN_USE",
        description=(
            "The number is already verified on another account; one number verifies one "
            "account. Contact support if it is yours."
        ),
    )
    SMS_NOT_SENT = strawberry.enum_value(
        "SMS_NOT_SENT",
        description="The SMS provider didn't accept the code message; try again later.",
    )
    PHONE_NOT_VERIFIED = strawberry.enum_value(
        "PHONE_NOT_VERIFIED",
        description=(
            "Posting a task, sending an offer and chatting need a verified mobile number; "
            "verify it with requestPhoneCode and verifyPhoneCode."
        ),
    )
    TERMS_UPDATE_REQUIRED = strawberry.enum_value(
        "TERMS_UPDATE_REQUIRED",
        description=(
            "The terms changed and the user hasn't accepted the version in force "
            "(`viewer.accountState` is TERMS_UPDATE_REQUIRED). Until they do they can't post, "
            "offer or book, but can view history, finish open bookings, export data and "
            "delete the account."
        ),
    )
    ACCOUNT_SUSPENDED = strawberry.enum_value(
        "ACCOUNT_SUSPENDED",
        description=(
            "The account is suspended: it can't post, offer or book, but can finish open "
            "bookings, appeal, export its data and delete the account."
        ),
    )
    ACCOUNT_DELETION_PENDING = strawberry.enum_value(
        "ACCOUNT_DELETION_PENDING",
        description=(
            "The account is waiting to be deleted once its open bookings and payouts close; "
            "it can't post, offer or book."
        ),
    )
    UPLOAD_CHANGED = strawberry.enum_value(
        "UPLOAD_CHANGED",
        description="The file was replaced after completeUpload; start a new upload.",
    )
    OUT_OF_SERVICE_AREA = strawberry.enum_value(
        "OUT_OF_SERVICE_AREA",
        description=(
            "The task's location is outside every active TaskBeavo service area: outside "
            "Croatia, or in a Croatian city TaskBeavo doesn't serve yet. Offer joinWaitlist."
        ),
    )
    WAITLIST_CONSENT_REQUIRED = strawberry.enum_value(
        "WAITLIST_CONSENT_REQUIRED",
        description="Joining the waitlist needs consent to the one launch email.",
    )
    INVALID_WAITLIST_DETAILS = strawberry.enum_value(
        "INVALID_WAITLIST_DETAILS",
        description="The city is missing or too long, or the country code isn't two letters.",
    )
    CARE_CONTACT_NOT_FOUND = strawberry.enum_value(
        "CARE_CONTACT_NOT_FOUND",
        description="No such on-site contact of the signed-in user.",
    )
    INVALID_CARE_CONTACT = strawberry.enum_value(
        "INVALID_CARE_CONTACT",
        description="The on-site contact's name or area is missing or longer than 100 characters.",
    )
    CARE_CONTACT_AGREEMENT_REQUIRED = strawberry.enum_value(
        "CARE_CONTACT_AGREEMENT_REQUIRED",
        description=(
            "Saving an on-site contact needs the poster's confirmation that the person agreed "
            "to having their details saved."
        ),
    )
    INVALID_CARE_CONTACT_PHONE = strawberry.enum_value(
        "INVALID_CARE_CONTACT_PHONE",
        description="The on-site contact's phone isn't a valid mobile or landline number.",
    )
    CARE_CONTACT_IN_USE = strawberry.enum_value(
        "CARE_CONTACT_IN_USE",
        description=(
            "An open task or an active booking uses this on-site contact; it can be deleted "
            "once the task or booking closes."
        ),
    )
    CARE_CONTACT_LOCKED = strawberry.enum_value(
        "CARE_CONTACT_LOCKED",
        description=(
            "A booking visit with this on-site contact has started; it can be changed again "
            "once the booking closes."
        ),
    )
    INVALID_OIB = strawberry.enum_value(
        "INVALID_OIB",
        description="The OIB isn't 11 digits with a matching check digit (ISO 7064 MOD 11,10).",
    )
    OIB_IN_USE = strawberry.enum_value(
        "OIB_IN_USE",
        description=(
            "The OIB is already on another helper's profile; one OIB belongs to one helper. "
            "Contact support if it is yours."
        ),
    )
    INVALID_BUSINESS_OIB = strawberry.enum_value(
        "INVALID_BUSINESS_OIB",
        description=(
            "A company's OIB can't be a person's OIB (the helper's own or another helper's), "
            "and an obrt's OIB is its owner's own OIB: leave it out for an obrt."
        ),
    )
    INVALID_IBAN = strawberry.enum_value(
        "INVALID_IBAN",
        description="The IBAN isn't a valid IBAN of a bank account in the EU.",
    )
    INVALID_VAT_ID = strawberry.enum_value(
        "INVALID_VAT_ID",
        description="The VAT ID isn't a valid EU VAT number (a Croatian one is HR and the OIB).",
    )
    INVALID_HELPER_DETAILS = strawberry.enum_value(
        "INVALID_HELPER_DETAILS",
        description=(
            "A helper profile field is missing, too long or invalid: a name, the street, "
            "city or Croatian postal code, the country code, an implausible date of birth, "
            "or business details without the BUSINESS status."
        ),
    )
    RESIDENCE_COUNTRY_NOT_ALLOWED = strawberry.enum_value(
        "RESIDENCE_COUNTRY_NOT_ALLOWED",
        description=(
            "The country of residence must be an EU member state, as an ISO 3166-1 alpha-2 "
            "code (HR, DE, AT, ...)."
        ),
    )
    HELPER_ADDRESS_NOT_IN_CROATIA = strawberry.enum_value(
        "HELPER_ADDRESS_NOT_IN_CROATIA",
        description=(
            "Helpers need a home address in Croatia; TaskBeavo isn't open to helpers "
            "elsewhere yet. Offer joinWaitlist."
        ),
    )
    SERVICE_AREA_NOT_FOUND = strawberry.enum_value(
        "SERVICE_AREA_NOT_FOUND",
        description="No such active service area; pick one from serviceAreas.",
    )
    HELPER_PROFILE_INCOMPLETE = strawberry.enum_value(
        "HELPER_PROFILE_INCOMPLETE",
        description=(
            "Sending offers needs a complete helper profile; `myHelperProfile.missing` "
            "lists what is still missing."
        ),
    )
    BUSINESS_ADDRESS_REQUIRED = strawberry.enum_value(
        "BUSINESS_ADDRESS_REQUIRED",
        description=(
            "An obrt or company sends offers only once its registered business address is "
            "on file: the contract confirmation shows it to the poster. "
            "`myHelperProfile.missing` lists BUSINESS_ADDRESS."
        ),
    )
    PAYOUT_ACCOUNT_NOT_READY = strawberry.enum_value(
        "PAYOUT_ACCOUNT_NOT_READY",
        description=(
            "Sending offers needs a Stripe payout account that can receive payments: "
            "finish Stripe onboarding, or give Stripe the information it asked for "
            "(startPayoutOnboarding). `myHelperProfile.payoutAccount` has the status."
        ),
    )
    PAYOUT_ACCOUNT_REJECTED = strawberry.enum_value(
        "PAYOUT_ACCOUNT_REJECTED",
        description=(
            "Stripe rejected the payout account, so onboarding can't continue; support "
            "explains why and how to appeal."
        ),
    )
    CATEGORY_NOT_ALLOWED = strawberry.enum_value(
        "CATEGORY_NOT_ALLOWED",
        description=(
            "TaskBeavo doesn't accept this kind of task: rides (driving people), medical care "
            "and personal care are not allowed."
        ),
    )
    INVALID_TASK_DETAILS = strawberry.enum_value(
        "INVALID_TASK_DETAILS",
        description=(
            "A required task field is missing (category, title, description, address, "
            "budget), a text is too long, the coordinates are invalid or there are more "
            "than 10 photos."
        ),
    )
    INVALID_TASK_DATES = strawberry.enum_value(
        "INVALID_TASK_DATES",
        description=(
            "The task's time window must start from now, end after it starts and end within "
            "60 days; a task without a flexible date needs one."
        ),
    )
    BUDGET_BELOW_MINIMUM = strawberry.enum_value(
        "BUDGET_BELOW_MINIMUM",
        description="The task's budget is below the minimum task price of EUR 10.",
    )
    TASK_NOT_FOUND = strawberry.enum_value(
        "TASK_NOT_FOUND",
        description="No such task, or not one the caller may see or change.",
    )
    TASK_NOT_EDITABLE = strawberry.enum_value(
        "TASK_NOT_EDITABLE",
        description=(
            "The task can't be changed this way: a draft is saved with saveTaskDraft and "
            "published once, and a closed task can't be changed."
        ),
    )
    TASK_NOT_OPEN_FOR_OFFERS = strawberry.enum_value(
        "TASK_NOT_OPEN_FOR_OFFERS",
        description=(
            "The task no longer takes offers: it is closed, booked, or its date has passed."
        ),
    )
    TASK_OUTSIDE_HELPER_AREAS = strawberry.enum_value(
        "TASK_OUTSIDE_HELPER_AREAS",
        description=(
            "The task lies outside the service areas on your helper profile; add its area "
            "with updateHelperProfile to offer on it."
        ),
    )
    OFFER_ON_OWN_TASK = strawberry.enum_value(
        "OFFER_ON_OWN_TASK", description="Helpers can't send offers on their own tasks."
    )
    OFFER_ALREADY_SENT = strawberry.enum_value(
        "OFFER_ALREADY_SENT",
        description="You already have an open offer on this task; change or renew that one.",
    )
    OFFER_NOT_FOUND = strawberry.enum_value(
        "OFFER_NOT_FOUND", description="No such offer of the signed-in helper."
    )
    OFFER_NOT_EDITABLE = strawberry.enum_value(
        "OFFER_NOT_EDITABLE",
        description=(
            "The offer is no longer open: it was withdrawn or has expired. An expired offer "
            "can be renewed."
        ),
    )
    OFFER_PRICE_BELOW_MINIMUM = strawberry.enum_value(
        "OFFER_PRICE_BELOW_MINIMUM",
        description="The offer's price is below the minimum task price of EUR 10.",
    )
    INVALID_OFFER_DETAILS = strawberry.enum_value(
        "INVALID_OFFER_DETAILS",
        description="The offer's price is too high or its message longer than 1000 characters.",
    )
    OFFER_MESSAGE_CONTACT_DETAILS = strawberry.enum_value(
        "OFFER_MESSAGE_CONTACT_DETAILS",
        description=(
            "The offer's message may contain a phone number, email address or link; remove "
            "it and send the offer again."
        ),
    )
    BUSINESS_POSTER_REGISTERED_ONLY = strawberry.enum_value(
        "BUSINESS_POSTER_REGISTERED_ONLY",
        description=(
            "The task's poster books for a business, so only registered helpers (obrt or "
            "company) can offer on it or be booked for it."
        ),
    )
    INVALID_POSTER_BUSINESS = strawberry.enum_value(
        "INVALID_POSTER_BUSINESS",
        description="The business kind is unknown, or its name is empty or over 200 characters.",
    )
    POSTER_BUSINESS_CHECKOUT_OPEN = strawberry.enum_value(
        "POSTER_BUSINESS_CHECKOUT_OPEN",
        description=(
            "You have an open checkout with a private helper; finish or leave it before "
            "adding your business."
        ),
    )
    TASK_BOOKED = strawberry.enum_value(
        "TASK_BOOKED",
        description=(
            "An offer on the task was accepted, so only its photos and, while the booking "
            "is active, its access details can change. A new date goes through a "
            "reschedule with the helper."
        ),
    )
    OFFER_NEEDS_UPDATE = strawberry.enum_value(
        "OFFER_NEEDS_UPDATE",
        description=(
            "The task changed since the helper sent this offer, and they haven't confirmed "
            "it yet; it can be accepted once they do."
        ),
    )
    OFFER_CHANGED = strawberry.enum_value(
        "OFFER_CHANGED",
        description="The helper changed the offer's price since you opened it; check it again.",
    )
    FEE_CHANGED = strawberry.enum_value(
        "FEE_CHANGED",
        description=(
            "The service fee changed since you opened the checkout (a new price list took "
            "effect); check the new total."
        ),
    )
    TASK_ALREADY_BOOKED = strawberry.enum_value(
        "TASK_ALREADY_BOOKED",
        description="The task already has a booking in progress; no other offer can be accepted.",
    )
    BOOKING_NOT_FOUND = strawberry.enum_value(
        "BOOKING_NOT_FOUND", description="No such booking of the signed-in user."
    )
    BOOKING_ACTION_NOT_ALLOWED = strawberry.enum_value(
        "BOOKING_ACTION_NOT_ALLOWED",
        description=(
            "The booking's status doesn't allow this action now, or not for you; refetch the "
            "booking to see its current status."
        ),
    )
    CHECKOUT_CLOSED = strawberry.enum_value(
        "CHECKOUT_CLOSED",
        description=(
            "The checkout is no longer open: it was paid, expired or ended because the offer "
            "was withdrawn. Refetch the booking to see which."
        ),
    )
    WORK_STARTED = strawberry.enum_value(
        "WORK_STARTED",
        description=(
            "The booking's start time has passed, so the poster can't cancel it themselves; "
            "open a dispute, or use the withdrawal button while it is offered."
        ),
    )
    INVALID_ETA = strawberry.enum_value(
        "INVALID_ETA",
        description=(
            "The arrival time isn't valid: missing, outside 1 to 240 minutes, or for running "
            "late, not after the booked start."
        ),
    )
    NO_FAULT_REPLY_CLOSED = strawberry.enum_value(
        "NO_FAULT_REPLY_CLOSED",
        description=(
            "The time to answer the no-fault request is over (the helper's 48 hours, or "
            "support decided), so the answer isn't taken. Refetch the booking."
        ),
    )
    CANCELLATION_CHANGED = strawberry.enum_value(
        "CANCELLATION_CHANGED",
        description=(
            "The refund changed since you opened the cancel screen (a deadline passed, such "
            "as 24 hours before the start); check it again. Nothing was cancelled."
        ),
    )
    WITHDRAWAL_NOT_AVAILABLE = strawberry.enum_value(
        "WITHDRAWAL_NOT_AVAILABLE",
        description=(
            "There is no withdrawal right to use on this booking: the helper is a private "
            "individual, or the 14-day withdrawal period is over. Cancel instead."
        ),
    )
    CONSENT_REQUIRED = strawberry.enum_value(
        "CONSENT_REQUIRED",
        description=(
            "Checkout needs both boxes ticked, the cancellation policy and the early-start "
            "request, for the cancellation policy version in force; reload it if it changed."
        ),
    )
    CONSENT_WORDING_CHANGED = strawberry.enum_value(
        "CONSENT_WORDING_CHANGED",
        description=(
            "The helper's private or business status changed since the checkout was shown, "
            "so the tick boxes read differently now; nothing was booked. Reload the offer "
            "and tick them again."
        ),
    )
    HELPER_CANNOT_TAKE_PAYMENTS = strawberry.enum_value(
        "HELPER_CANNOT_TAKE_PAYMENTS",
        description=(
            "The helper's payout account can't receive payments right now, so nothing was "
            "charged; the offer stays open. Try again later or choose another offer."
        ),
    )
    OFFER_NOT_AVAILABLE = strawberry.enum_value(
        "OFFER_NOT_AVAILABLE",
        description=(
            "This offer can no longer be accepted, so nothing was charged; choose another offer."
        ),
    )
    PAYMENT_UNAVAILABLE = strawberry.enum_value(
        "PAYMENT_UNAVAILABLE",
        description=(
            "The payment couldn't be started, so nothing was charged; the offer stays open. "
            "Try again."
        ),
    )


class ApiError(GraphQLError):
    """An error a resolver raises for the client to translate: the code goes in extensions.

    `retry_after_seconds`, when given, is sent as `extensions.retryAfterSeconds`.
    """

    def __init__(
        self, code: ErrorCode, message: str, *, retry_after_seconds: int | None = None
    ) -> None:
        extensions: dict[str, object] = {"code": code.name}
        if retry_after_seconds is not None:
            extensions["retryAfterSeconds"] = retry_after_seconds
        super().__init__(message, extensions=extensions)
        self.code = code


class SafeMessageError(Exception):
    """An error whose message is safe to send to Sentry as written.

    Contract: build the message only from error codes, IDs (primary keys, booking, task
    and Stripe IDs) and enum values. Never from user input, request data, a model's text
    fields (names, addresses, descriptions, chat) or another exception's message. Sentry
    receives the message of this class and its subclasses unchanged (config/scrubbing.py);
    every other exception message, including Django's and Postgres's, becomes
    "[Filtered]".

        raise PayoutFailedError(
            f"Booking {booking.pk} payout is {booking.status}", ids={"booking.id": booking.pk}
        )

    `code` and `ids` become Sentry tags, so the event can be found by them. `ids` keys
    come from the telemetry allow-list (`booking.id`, `stripe.event_id`, ... in
    config/telemetry.py); other keys are dropped.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | str | None = None,
        ids: Mapping[str, int | str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code.name if isinstance(code, ErrorCode) else code
        self.ids = dict(ids or {})
