"""Outbox handlers of the payments app (imported by PaymentsConfig.ready).

A helper hears about their own payout account by push and email: when Stripe needs
more information (and in the reminders after that), and when Stripe rejects it. Both are
service messages, so they can't be switched off (notifications.models.Category.PAYMENT).
The handler reloads the account: a status that moved on before the row was published
sends nothing.
"""

from core.models import Outbox
from core.outbox import register_handler
from notifications.dispatch import notify
from payments.connect import INFO_REMINDER_TOPIC, STATUS_CHANGED_TOPIC
from payments.models import ConnectedAccount

_KINDS = {
    ConnectedAccount.Status.NEEDS_INFO: "payouts.info_needed",
    ConnectedAccount.Status.REJECTED: "payouts.rejected",
}


@register_handler(STATUS_CHANGED_TOPIC)
@register_handler(INFO_REMINDER_TOPIC)
def notify_helper(row: Outbox) -> None:
    account = ConnectedAccount.objects.select_related("helper").get(pk=row.aggregate_id)
    if account.status != row.payload["status"]:
        return
    kind = _KINDS.get(ConnectedAccount.Status(account.status))
    if kind is not None:
        notify(row, kind, [account.helper.user_id])
