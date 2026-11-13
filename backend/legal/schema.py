"""GraphQL API of legal documents: which version is in force (legal/services.py)."""

import datetime as dt
import uuid
from enum import Enum
from typing import Any

import strawberry
from asgiref.sync import sync_to_async
from django.conf import settings
from strawberry.types import Info

from accounts.geo import geoip_available
from accounts.models import Session, User
from accounts.schema import Locale
from core.errors import ApiError, ErrorCode
from legal import consents
from legal.checkout_consent import CheckoutConsentText, checkout_consent_texts, without_markers
from legal.models import Consent, LegalDocument
from legal.services import current_document, readable_documents


@strawberry.enum(description="A kind of legal document.")
class LegalDocumentKind(Enum):
    TERMS = LegalDocument.Kind.TERMS.value
    HELPER_TERMS = LegalDocument.Kind.HELPER_TERMS.value
    PRIVACY = LegalDocument.Kind.PRIVACY.value
    CANCELLATION = LegalDocument.Kind.CANCELLATION.value
    COOKIES = LegalDocument.Kind.COOKIES.value
    IMPRINT = LegalDocument.Kind.IMPRINT.value


@strawberry.type(
    name="CheckoutConsentText",
    description=(
        "The wording of the two checkout tick boxes for one helper type, as the lawyer "
        "wrote it in this CANCELLATION version and language. Show it as it is; never "
        "translate it."
    ),
)
class CheckoutConsentTextType:
    cancellation_policy: str = strawberry.field(
        description="The box accepting the cancellation and refund policy."
    )
    early_start: str = strawberry.field(
        description=(
            "The box asking for the start within the 14-day withdrawal period, with the "
            "acknowledgement that the right is lost once the service is fully performed."
        )
    )

    @classmethod
    def of(cls, text: CheckoutConsentText) -> "CheckoutConsentTextType":
        return cls(cancellation_policy=text.cancellation_policy, early_start=text.early_start)


@strawberry.type(
    name="CheckoutConsentTexts",
    description=(
        "The checkout tick boxes per helper type: show `privateHelper` for an offer from a "
        "private individual, `traderHelper` for a registered business, and send that "
        "variant with the consent."
    ),
)
class CheckoutConsentTextsType:
    private_helper: CheckoutConsentTextType
    trader_helper: CheckoutConsentTextType


@strawberry.type(
    name="LegalDocument",
    description="One language of one published version of a legal document. Immutable.",
)
class LegalDocumentType:
    id: strawberry.ID
    kind: LegalDocumentKind
    version: str = strawberry.field(
        description="The same in every language; what sign-up sends as acceptedTermsVersion."
    )
    language: Locale
    effective_at: dt.datetime = strawberry.field(description="When this version took effect.")
    text: str = strawberry.field(
        description="The document's Markdown as published, for reading it in the app."
    )
    is_draft: bool = strawberry.field(
        description=(
            "Placeholder text no lawyer has approved; show it clearly marked. Never served "
            "where drafts aren't allowed (production)."
        )
    )
    checkout_consent: CheckoutConsentTextsType | None = strawberry.field(
        description="The checkout tick-box texts; CANCELLATION documents only."
    )

    @classmethod
    def of(cls, document: LegalDocument) -> "LegalDocumentType":
        consent = (
            checkout_consent_texts(document.body)
            if document.kind == LegalDocument.Kind.CANCELLATION
            else None
        )
        return cls(
            id=strawberry.ID(str(document.pk)),
            kind=LegalDocumentKind(document.kind),
            version=document.version,
            language=Locale(document.language),
            effective_at=document.effective_at,
            text=without_markers(document.body),
            is_draft=document.is_draft,
            checkout_consent=(
                CheckoutConsentTextsType(
                    private_helper=CheckoutConsentTextType.of(consent.private),
                    trader_helper=CheckoutConsentTextType.of(consent.trader),
                )
                if consent
                else None
            ),
        )


@strawberry.enum(description="An optional use of personal data that needs the user's consent.")
class ConsentPurpose(Enum):
    ANALYTICS = Consent.Purpose.ANALYTICS.value
    MARKETING_EMAIL = Consent.Purpose.MARKETING_EMAIL.value
    MARKETING_PUSH = Consent.Purpose.MARKETING_PUSH.value


@strawberry.type(
    name="ConsentState",
    description=(
        "The current choice about one purpose: the latest CONSENT row, or off while the "
        "user never chose."
    ),
)
class ConsentStateType:
    id: strawberry.ID = strawberry.field(description='The purpose, e.g. "ANALYTICS".')
    purpose: ConsentPurpose
    granted: bool
    version: str | None = strawberry.field(
        description=(
            "The cookie policy (analytics) or privacy notice (marketing) version the choice "
            "referred to; null before any choice or for a refusal while none was in force."
        )
    )
    given_at: dt.datetime | None

    @classmethod
    def of(cls, purpose: str, row: Consent | None) -> "ConsentStateType":
        return cls(
            id=strawberry.ID(purpose),
            purpose=ConsentPurpose(purpose),
            granted=row.granted if row else False,
            version=(row.version or None) if row else None,
            given_at=row.given_at if row else None,
        )


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            "The signed-in user's analytics and marketing choices, one per purpose, all off "
            "until they agree. Errors: UNAUTHENTICATED."
        )
    )
    async def consents(self, info: Info) -> list[ConsentStateType]:
        user = _signed_in_user(info)
        found = await sync_to_async(consents.current_consents)(user)
        return [ConsentStateType.of(purpose, row) for purpose, row in found.items()]

    @strawberry.field(
        description=(
            "The version of a legal document in force now, in `language`; null while none "
            "is. Public: sign-up shows and accepts it before the account exists."
        )
    )
    async def current_legal_document(
        self, kind: LegalDocumentKind, language: Locale
    ) -> LegalDocumentType | None:
        document = await sync_to_async(current_document)(kind.value, language.value)
        if document is None or (document.is_draft and not settings.LEGAL_DRAFTS_ALLOWED):
            return None
        return LegalDocumentType.of(document)

    @strawberry.field(
        description=(
            "One published version of a legal document in `language`, current, earlier or "
            "scheduled; null when it doesn't exist or was withdrawn. Public (legal pages)."
        )
    )
    async def legal_document(
        self, kind: LegalDocumentKind, language: Locale, version: str
    ) -> LegalDocumentType | None:
        document = await sync_to_async(
            lambda: readable_documents(kind.value, language.value).filter(version=version).first()
        )()
        return LegalDocumentType.of(document) if document else None

    @strawberry.field(
        description=(
            "Every readable version of a legal document in `language`, newest effective date "
            "first, including scheduled ones; the legal pages link them. Public."
        )
    )
    async def legal_document_versions(
        self, kind: LegalDocumentKind, language: Locale
    ) -> list[LegalDocumentType]:
        documents = await sync_to_async(
            lambda: list(readable_documents(kind.value, language.value))
        )()
        return [LegalDocumentType.of(document) for document in documents]

    @strawberry.field(
        name="usesGeolite2",
        description=(
            "Whether the API looks up locations in MaxMind GeoLite2 (GEOIP_CITY_DATABASE "
            "and the MaxMind account are configured); the imprint and the privacy notice "
            "then show MaxMind's attribution, as the GeoLite EULA requires."
        ),
    )
    def uses_geolite2(self) -> bool:
        return geoip_available()


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Record a choice about analytics or news and offers by email or push, as a new "
            "CONSENT row with the cookie policy or privacy notice version in force in "
            "`language`. Signed out, only the web cookie banner's ANALYTICS choice is "
            "accepted, with the browser's random `visitorId` (a UUID). Errors: "
            "UNAUTHENTICATED, CONSENT_UNAVAILABLE (agreeing while no policy is in force)."
        )
    )
    async def set_consent(
        self,
        info: Info,
        purpose: ConsentPurpose,
        granted: bool,
        language: Locale,
        visitor_id: str | None = None,
    ) -> ConsentStateType:
        user = info.context.user if isinstance(info.context.user, User) else None
        session = getattr(info.context, "session", None)
        platform = session.platform if isinstance(session, Session) else Session.Platform.WEB
        row = await sync_to_async(consents.record_consent)(
            purpose=purpose.value,
            granted=granted,
            language=language.value,
            platform=platform,
            user=user,
            visitor_id=_visitor(visitor_id),
        )
        return ConsentStateType.of(purpose.value, row)


def _signed_in_user(info: Info[Any, Any]) -> User:
    user = info.context.user
    if not isinstance(user, User):
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return user


def _visitor(value: str | None) -> uuid.UUID | None:
    """The browser's random id; anything that isn't a UUID counts as none."""
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None
