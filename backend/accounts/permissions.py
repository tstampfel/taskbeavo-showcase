"""Permission guards for mutations that create work or contact other people.

`postTask`, `sendOffer` and `sendMessage` need a verified mobile number (plan: "Sign-up
and login"), and the mutations that create work also need an account that isn't in
limited mode (`CanCreateWork`, which includes the phone check). Apply the guard on the
mutation, not in screens:

    @strawberry.mutation(permission_classes=[PhoneVerified])
    def post_task(self, info: Info, ...) -> TaskType: ...

or call `require_verified_phone(info.context.user)` first thing in the resolver (or in
the service function it calls) when the check must sit next to other checks.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from strawberry.permission import BasePermission
from strawberry.types import Info

from accounts.models import User
from accounts.states import require_can_create_work
from core.errors import ApiError, ErrorCode


def require_signed_in(user: AbstractBaseUser | AnonymousUser | None) -> User:
    """The signed-in, active user; raises UNAUTHENTICATED otherwise."""
    if not isinstance(user, User) or not user.is_active:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return user


def require_verified_phone(user: AbstractBaseUser | AnonymousUser | None) -> User:
    """The signed-in user if their mobile number is verified.

    Raises UNAUTHENTICATED when signed out and PHONE_NOT_VERIFIED when unverified.
    """
    user = require_signed_in(user)
    if user.phone_verified_at is None:
        raise ApiError(ErrorCode.PHONE_NOT_VERIFIED, "Verify your mobile number first.")
    return user


class PhoneVerified(BasePermission):
    """Strawberry permission: the field answers UNAUTHENTICATED or PHONE_NOT_VERIFIED.

    The request's user is loaded fresh for every request (config/views.py), so a number
    verified a moment ago counts at once, and the check needs no query: it is safe in
    sync and async resolvers alike.
    """

    message = "Verify your mobile number first."

    def has_permission(self, source: Any, info: Info, **kwargs: Any) -> bool:
        # ApiError carries the code; raising it gives the client the same error as
        # `require_verified_phone` instead of Strawberry's generic permission error.
        require_verified_phone(info.context.user)
        return True


class CanCreateWork(BasePermission):
    """Strawberry permission for every mutation that creates new work: postTask,
    publishTaskDraft, sendOffer, updateOffer, renewOffer and acceptOffer.

    In order it answers UNAUTHENTICATED when signed out, the account state's code when
    the account is in limited mode (TERMS_UPDATE_REQUIRED, ACCOUNT_SUSPENDED,
    ACCOUNT_DELETION_PENDING; `accounts.states`), and PHONE_NOT_VERIFIED when the
    mobile number is unverified. Finishing open bookings, chat, exports and deletion
    don't use it, so limited accounts keep them. The state check queries the database,
    so the permission is async and the resolver it guards must be too.
    """

    message = "This account can't create new work."

    async def has_permission(self, source: Any, info: Info, **kwargs: Any) -> bool:
        user = require_signed_in(info.context.user)
        await sync_to_async(require_can_create_work)(user)
        require_verified_phone(user)
        return True
