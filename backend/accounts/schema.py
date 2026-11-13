"""GraphQL API of sign-in and sessions. The flow is described in accounts/signin.py."""

import datetime as dt
import hashlib
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Any

import strawberry
from asgiref.sync import sync_to_async
from strawberry.types import Info

from accounts import business as business_module
from accounts import phone as phone_module
from accounts import providers, sessions, signin, states
from accounts.models import PosterBusiness, ProviderIdentity, Session, User
from config.graphql_limits import http_client_ip
from core.errors import ApiError, ErrorCode
from helpers.oib import masked_oib

if TYPE_CHECKING:
    from legal.schema import LegalDocumentType


@strawberry.enum(description="A language TaskBeavo is available in.")
class Locale(Enum):
    HR = "hr"
    EN = "en"
    DE = "de"
    FR = "fr"


@strawberry.enum(description="Where a session signed in.")
class SessionPlatform(Enum):
    WEB = Session.Platform.WEB.value
    IOS = Session.Platform.IOS.value
    ANDROID = Session.Platform.ANDROID.value


@strawberry.enum(description="A sign-in provider.")
class SignInProvider(Enum):
    GOOGLE = ProviderIdentity.Provider.GOOGLE.value
    APPLE = ProviderIdentity.Provider.APPLE.value


AccountState = strawberry.enum(
    states.AccountState,
    description=(
        "What the account may do. Anything but ACTIVE is limited mode: no new tasks, "
        "offers or bookings, while history, open bookings, chat on them, data export and "
        "account deletion keep working."
    ),
)


@strawberry.enum(description="The kind of registered business a poster books for.")
class PosterBusinessKind(Enum):
    OBRT = PosterBusiness.Kind.OBRT.value
    COMPANY = PosterBusiness.Kind.COMPANY.value


@strawberry.type(
    name="PosterBusiness",
    description=(
        "The obrt or company the signed-in poster books for. While it is set, their tasks "
        "take offers from registered helpers only. Only its owner sees it."
    ),
)
class PosterBusinessType:
    id: strawberry.ID
    kind: PosterBusinessKind
    name: str
    oib_masked: str = strawberry.field(description="The OIB, all but its last four digits hidden.")

    @classmethod
    def of(cls, business: PosterBusiness) -> "PosterBusinessType":
        return cls(
            id=strawberry.ID(str(business.pk)),
            kind=PosterBusinessKind(business.kind),
            name=business.name,
            oib_masked=masked_oib(business.oib),
        )


@strawberry.input
class PosterBusinessInput:
    kind: PosterBusinessKind
    name: str = strawberry.field(description="The obrt's or company's name, up to 200 characters.")
    oib: str = strawberry.field(
        description="The company's OIB, or for an obrt its owner's own OIB; 11 digits."
    )


@strawberry.type(name="User", description="The signed-in account, as its owner sees it.")
class UserType:
    id: strawberry.ID
    email: str
    first_name: str
    last_name: str
    locale: Locale
    country: str = strawberry.field(description="ISO 3166-1 alpha-2 country code.")
    time_zone: str = strawberry.field(description='IANA time zone, e.g. "Europe/Berlin".')
    phone_verified: bool = strawberry.field(
        description="Whether the mobile number is verified; posting, offering and chat need it."
    )
    phone_number_masked: str | None = strawberry.field(
        description=(
            'The verified mobile number, masked ("+385 ••• 678"); null until verified. '
            "Only its owner ever sees it."
        )
    )
    _user: strawberry.Private[User]

    @strawberry.field(
        description=(
            "ACTIVE, or the limited mode the account is in: TERMS_UPDATE_REQUIRED until the "
            "terms version in force is accepted, SUSPENDED, or DELETION_PENDING."
        )
    )
    async def account_state(self) -> AccountState:  # pyright: ignore[reportInvalidTypeForm]
        return await sync_to_async(states.account_state)(self._user)

    @strawberry.field(
        description=(
            "Whether the terms version in force still has to be accepted: show \"We've "
            'updated our terms" with `termsToAccept` and call '
            "acceptCurrentTerms. True also while the account is SUSPENDED or "
            "DELETION_PENDING, whose state `accountState` reports first."
        )
    )
    async def must_accept_terms(self) -> bool:
        return await sync_to_async(states.must_accept_terms)(self._user)

    @strawberry.field(
        description=(
            'What "We\'ve updated our terms" shows, in `language`: the version in force '
            "while it still has to be accepted, null otherwise. The tick box names its "
            "`version` and `effectiveAt`; send that version to acceptCurrentTerms. Its "
            "`kind` says which document it is (TERMS today; HELPER_TERMS once helpers "
            "re-accept helper terms)."
        )
    )
    async def terms_to_accept(
        self, language: Locale
    ) -> Annotated["LegalDocumentType", strawberry.lazy("legal.schema")] | None:
        from legal.schema import LegalDocumentType

        document = await sync_to_async(states.terms_to_accept)(self._user, language.value)
        return LegalDocumentType.of(document) if document else None

    @strawberry.field(
        description=(
            "The obrt or company you book for, or null. While it is set, your tasks take "
            "offers from registered helpers only."
        )
    )
    async def poster_business(self) -> PosterBusinessType | None:
        business = await sync_to_async(business_module.poster_business)(self._user)
        return PosterBusinessType.of(business) if business else None

    @classmethod
    def of(cls, user: User) -> "UserType":
        return cls(
            _user=user,
            phone_verified=user.phone_verified_at is not None,
            phone_number_masked=phone_module.masked_phone(user),
            id=strawberry.ID(str(user.pk)),
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            locale=Locale(user.locale),
            country=user.country,
            time_zone=user.time_zone,
        )


@strawberry.type(
    description=(
        "A signed-in session's tokens. Send the access token as `Authorization: Bearer`; "
        "trade the refresh token for new tokens with `refreshSession` before it expires."
    )
)
class AuthSession:
    id: strawberry.ID
    access_token: str
    access_token_expires_at: dt.datetime
    refresh_token: str
    refresh_token_expires_at: dt.datetime
    device_token: str | None = strawberry.field(
        description=(
            "Keep it on this device, apart from the session (it outlives sign-out), and send "
            "it as `DeviceInput.deviceToken` on the next sign-in: a sign-in without one this "
            "account has seen emails a new-device notice. Null on refreshSession."
        )
    )
    user: UserType

    @classmethod
    def of(cls, issued: sessions.IssuedTokens) -> "AuthSession":
        return cls(
            id=strawberry.ID(str(issued.session.pk)),
            access_token=issued.access_token,
            access_token_expires_at=issued.access_token_expires_at,
            refresh_token=issued.refresh_token,
            refresh_token_expires_at=issued.refresh_token_expires_at,
            device_token=issued.device_token,
            user=UserType.of(issued.session.user),
        )


@strawberry.type(
    description=(
        "The email is verified but has no account yet: send `ticket` with the account "
        "details to `completeAccountDetails`."
    )
)
class AccountDetailsRequired:
    id: strawberry.ID = strawberry.field(description="A digest of the ticket, for the cache.")
    email: str = strawberry.field(
        description=(
            'The address the account will have. After Apple\'s "Hide My Email" it is a '
            "private relay address."
        )
    )
    ticket: str
    first_name: str | None = strawberry.field(
        description="The name Google or Apple shared, to pre-fill the form; null if none."
    )
    last_name: str | None = strawberry.field(
        description="The family name Google or Apple shared; null if none."
    )

    @classmethod
    def of(cls, result: signin.AccountDetailsRequired) -> "AccountDetailsRequired":
        digest = hashlib.sha256(result.ticket.encode()).hexdigest()[:32]
        return cls(
            id=strawberry.ID(digest),
            email=result.email,
            ticket=result.ticket,
            first_name=result.first_name or None,
            last_name=result.last_name or None,
        )


@strawberry.type(
    description=(
        "The Google or Apple email already has an account: a 6-digit code was emailed to it. "
        "Send `ticket` and the code to `linkProviderWithEmailCode` to add this sign-in to "
        "the account. Accounts are never linked on the provider's word alone."
    )
)
class ProviderLinkRequired:
    id: strawberry.ID = strawberry.field(description="A digest of the ticket, for the cache.")
    email: str = strawberry.field(description="The account's address the code went to.")
    ticket: str

    @classmethod
    def of(cls, result: signin.ProviderLinkRequired) -> "ProviderLinkRequired":
        digest = hashlib.sha256(result.ticket.encode()).hexdigest()[:32]
        return cls(id=strawberry.ID(digest), email=result.email, ticket=result.ticket)


@strawberry.type(description="A one-time nonce for a Google or Apple sign-in.")
class SignInNonce:
    id: strawberry.ID = strawberry.field(description="A digest of the nonce, for the cache.")
    nonce: str = strawberry.field(
        description=(
            "Pass it to Google or Apple as the sign-in nonce (as is, or its SHA-256 hex "
            "digest), then send it with the ID token to signInWithProvider."
        )
    )
    expires_at: dt.datetime


@strawberry.type(description="An SMS code is on its way to the number.")
class PhoneCodeSent:
    id: strawberry.ID
    phone_number_masked: str = strawberry.field(description='E.g. "+49 ••• 789".')
    expires_at: dt.datetime
    resend_available_at: dt.datetime = strawberry.field(
        description="When requestPhoneCode may be called again."
    )


SignInResult = Annotated[AuthSession | AccountDetailsRequired, strawberry.union("SignInResult")]
ProviderSignInResult = Annotated[
    AuthSession | ProviderLinkRequired | AccountDetailsRequired,
    strawberry.union("ProviderSignInResult"),
]


@strawberry.input
class AccountDetailsInput:
    ticket: str
    first_name: str
    last_name: str
    is_adult: bool = strawberry.field(description="The person confirmed they are 18 or older.")
    locale: Locale = strawberry.field(description="The language the person chose for the app.")
    country: str = strawberry.field(description="ISO 3166-1 alpha-2 country they live in.")
    time_zone: str = strawberry.field(description="The device's IANA time zone.")
    accepted_terms_version: str | None = strawberry.field(
        default=None,
        description=(
            "Required: the version of the terms and conditions the person ticked the box "
            "for, from `currentLegalDocument(kind: TERMS)`. Optional in the schema only so "
            "that leaving it out answers TERMS_NOT_ACCEPTED."
        ),
    )


@strawberry.input
class ProviderSignInInput:
    provider: SignInProvider
    id_token: str = strawberry.field(description="The ID token Google or Apple returned.")
    nonce: str = strawberry.field(description="The nonce from requestSignInNonce, unhashed.")
    first_name: str = strawberry.field(
        default="",
        description="Apple only: the given name Apple handed the app on the first sign-in.",
    )
    last_name: str = strawberry.field(
        default="",
        description="Apple only: the family name Apple handed the app on the first sign-in.",
    )


@strawberry.input
class DeviceInput:
    platform: SessionPlatform
    name: str = strawberry.field(default="", description='E.g. "iPhone" or "Firefox on Mac".')
    device_token: str | None = strawberry.field(
        default=None,
        description="The `AuthSession.deviceToken` this device kept from its last sign-in.",
    )


@strawberry.type
class Query:
    @strawberry.field(description="The signed-in user, or null when signed out.")
    def viewer(self, info: Info) -> UserType | None:
        user = info.context.user
        return UserType.of(user) if isinstance(user, User) else None


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Email a 6-digit sign-in code, valid 10 minutes. Always returns true, whether or "
            "not the address has an account. `locale` is the app's current language, used "
            "for the email when the address has no account yet. At most 5 per address per "
            "hour. Errors: INVALID_EMAIL, RATE_LIMITED and LOCKED (both with "
            "extensions.retryAfterSeconds)."
        )
    )
    async def request_email_code(self, info: Info, email: str, locale: Locale) -> bool:
        await sync_to_async(signin.request_email_code)(email, locale.value, _client_ip(info))
        return True

    @strawberry.mutation(
        description=(
            "Check a sign-in code. An existing account gets a session; a new address gets "
            "AccountDetailsRequired. A sign-in from a new device emails the account a notice. "
            "Errors: INVALID_EMAIL, INVALID_CODE, LOCKED (5 wrong codes lock the address for "
            "30 minutes, with extensions.retryAfterSeconds)."
        )
    )
    async def verify_email_code(
        self, info: Info, email: str, code: str, device: DeviceInput
    ) -> SignInResult:
        result = await sync_to_async(signin.verify_email_code)(
            email, code, device.platform.value, device.name, _client_ip(info), device.device_token
        )
        if isinstance(result, signin.AccountDetailsRequired):
            return AccountDetailsRequired.of(result)
        return AuthSession.of(result)

    @strawberry.mutation(
        description=(
            "A one-time nonce, valid until its expiresAt, to start a Google or Apple sign-in."
        )
    )
    async def request_sign_in_nonce(self) -> SignInNonce:
        issued = await sync_to_async(providers.issue_nonce)()
        digest = hashlib.sha256(issued.nonce.encode()).hexdigest()[:32]
        return SignInNonce(
            id=strawberry.ID(digest), nonce=issued.nonce, expires_at=issued.expires_at
        )

    @strawberry.mutation(
        description=(
            "Sign in with a Google or Apple ID token. A known provider account gets a "
            "session; an email that already has an account gets ProviderLinkRequired (a code "
            "is emailed to it); a new one gets AccountDetailsRequired, then "
            "completeAccountDetails as for email. Errors: INVALID_PROVIDER_TOKEN, "
            "PROVIDER_UNAVAILABLE, PROVIDER_EMAIL_MISSING, and RATE_LIMITED or LOCKED when a "
            "link code can't be sent."
        )
    )
    async def sign_in_with_provider(
        self, info: Info, input: ProviderSignInInput, device: DeviceInput
    ) -> ProviderSignInResult:
        result = await sync_to_async(signin.sign_in_with_provider)(
            provider=input.provider.value,
            id_token=input.id_token,
            nonce=input.nonce,
            platform=device.platform.value,
            device_name=device.name,
            first_name=input.first_name,
            last_name=input.last_name,
            client_ip=_client_ip(info),
            device_token=device.device_token,
        )
        if isinstance(result, signin.AccountDetailsRequired):
            return AccountDetailsRequired.of(result)
        if isinstance(result, signin.ProviderLinkRequired):
            return ProviderLinkRequired.of(result)
        return AuthSession.of(result)

    @strawberry.mutation(
        description=(
            "Add the Google or Apple sign-in from ProviderLinkRequired to the account, using "
            "the code emailed to it, and sign in. Errors: INVALID_CODE, LOCKED, "
            "SIGNUP_TICKET_EXPIRED (start the Google or Apple sign-in again)."
        )
    )
    async def link_provider_with_email_code(
        self, info: Info, ticket: str, code: str, device: DeviceInput
    ) -> AuthSession:
        issued = await sync_to_async(signin.link_provider_with_email_code)(
            ticket, code, device.platform.value, device.name, _client_ip(info), device.device_token
        )
        return AuthSession.of(issued)

    @strawberry.mutation(
        description=(
            "Create the account and sign in, recording that it accepted the terms in force. "
            "Errors: SIGNUP_TICKET_EXPIRED, UNDERAGE, INVALID_ACCOUNT_DETAILS, "
            "TERMS_NOT_ACCEPTED."
        )
    )
    async def complete_account_details(
        self, info: Info, details: AccountDetailsInput, device: DeviceInput
    ) -> AuthSession:
        issued = await sync_to_async(signin.complete_account_details)(
            ticket=details.ticket,
            first_name=details.first_name,
            last_name=details.last_name,
            is_adult=details.is_adult,
            locale=details.locale.value,
            country=details.country,
            time_zone=details.time_zone,
            platform=device.platform.value,
            accepted_terms_version=details.accepted_terms_version,
            device_name=device.name,
            client_ip=_client_ip(info),
            device_token=device.device_token,
        )
        return AuthSession.of(issued)

    @strawberry.mutation(
        description=(
            "Trade a refresh token for new tokens; the old one stops working. Errors: "
            "SESSION_EXPIRED (sign in again)."
        )
    )
    async def refresh_session(self, refresh_token: str) -> AuthSession:
        return AuthSession.of(await sync_to_async(sessions.refresh_session)(refresh_token))

    @strawberry.mutation(
        description=(
            "Sign out this session and close its open subscription WebSockets. Always returns true."
        )
    )
    async def sign_out(self, refresh_token: str) -> bool:
        await sync_to_async(sessions.revoke_session)(refresh_token)
        return True

    @strawberry.mutation(
        description=(
            "Log out of all devices: revoke every session of the signed-in user, this one "
            "included, and close their open subscription WebSockets. Always returns true. "
            "Errors: UNAUTHENTICATED."
        )
    )
    async def sign_out_everywhere(self, info: Info) -> bool:
        user = _signed_in_user(info)
        await sync_to_async(sessions.revoke_all_sessions)(user)
        return True

    @strawberry.mutation(
        description=(
            "\"This wasn't me\": the token from the new-device email's link logs that account "
            "out of all devices, without signing in. Always returns true when the token is "
            "good. Errors: SIGN_OUT_LINK_EXPIRED."
        )
    )
    async def sign_out_everywhere_from_email(self, token: str) -> bool:
        await sync_to_async(sessions.revoke_all_from_link)(token)
        return True

    @strawberry.mutation(
        description=(
            "Save the language the signed-in user chose; emails, push and SMS follow it. "
            "Errors: UNAUTHENTICATED."
        )
    )
    async def set_locale(self, info: Info, locale: Locale) -> UserType:
        user = _signed_in_user(info)
        user.locale = locale.value
        await user.asave(update_fields=["locale"])
        return UserType.of(user)

    @strawberry.mutation(
        description=(
            'Accept the updated terms from "We\'ve updated our terms": `version` is '
            "`currentLegalDocument(kind: TERMS)`'s version and `language` the language it "
            "was shown in. Ends TERMS_UPDATE_REQUIRED; accepting a version again changes "
            "nothing. Errors: UNAUTHENTICATED, TERMS_NOT_ACCEPTED (the version isn't the "
            "one in force any more: fetch it and show it again)."
        )
    )
    async def accept_current_terms(self, info: Info, version: str, language: Locale) -> UserType:
        user = _signed_in_user(info)
        user = await sync_to_async(states.accept_current_terms)(
            user, version, language.value, _signed_in_session(info).platform
        )
        return UserType.of(user)

    @strawberry.mutation(
        description=(
            "Text a 6-digit code to the signed-in user's mobile number, valid 10 minutes. "
            "`phone` is international (+49 151 23456789) or national in the account's "
            "country. Errors: UNAUTHENTICATED, INVALID_PHONE_NUMBER, "
            "PHONE_COUNTRY_NOT_SUPPORTED, RATE_LIMITED (with extensions.retryAfterSeconds), "
            "SMS_NOT_SENT."
        )
    )
    async def request_phone_code(self, info: Info, phone: str) -> PhoneCodeSent:
        user = _signed_in_user(info)
        sent = await sync_to_async(phone_module.request_phone_code)(user, phone, _client_ip(info))
        return PhoneCodeSent(
            id=strawberry.ID(str(sent.id)),
            phone_number_masked=sent.phone_masked,
            expires_at=sent.expires_at,
            resend_available_at=sent.resend_available_at,
        )

    @strawberry.mutation(
        description=(
            "Check the SMS code from requestPhoneCode and save the number as verified. "
            "Errors: UNAUTHENTICATED, INVALID_CODE, PHONE_NUMBER_IN_USE, RATE_LIMITED."
        )
    )
    async def verify_phone_code(self, info: Info, code: str) -> UserType:
        user = _signed_in_user(info)
        return UserType.of(await sync_to_async(phone_module.verify_phone_code)(user, code))

    @strawberry.mutation(
        description=(
            "Book for your obrt or company. From then on your tasks take offers from "
            "registered helpers only: private helpers' open offers on them are withdrawn "
            "and they are told why. Errors: UNAUTHENTICATED, INVALID_POSTER_BUSINESS, "
            "INVALID_OIB, INVALID_BUSINESS_OIB, POSTER_BUSINESS_CHECKOUT_OPEN."
        )
    )
    async def set_poster_business(self, info: Info, input: PosterBusinessInput) -> UserType:
        user = _signed_in_user(info)
        await sync_to_async(business_module.set_poster_business)(
            user, kind=input.kind.value, name=input.name, oib=input.oib
        )
        return UserType.of(user)

    @strawberry.mutation(
        description=(
            "Stop booking for a business. Offers withdrawn meanwhile stay withdrawn. "
            "Errors: UNAUTHENTICATED."
        )
    )
    async def clear_poster_business(self, info: Info) -> UserType:
        user = _signed_in_user(info)
        await sync_to_async(business_module.clear_poster_business)(user)
        return UserType.of(user)

    @strawberry.mutation(
        description=(
            "A token for the subscriptions WebSocket's connection_init `authToken`, valid "
            "about a minute. For the web app, whose access token page code can't read. "
            "Errors: UNAUTHENTICATED."
        )
    )
    def web_socket_token(self, info: Info) -> str:
        return sessions.issue_websocket_token(_signed_in_session(info))


def _signed_in_user(info: Info[Any, Any]) -> User:
    user = info.context.user
    if not isinstance(user, User):
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return user


def _client_ip(info: Info[Any, Any]) -> str:
    return http_client_ip(info.context.request.META)


def _signed_in_session(info: Info[Any, Any]) -> Session:
    session = getattr(info.context, "session", None)
    if not isinstance(session, Session):
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return session
