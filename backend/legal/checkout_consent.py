"""The checkout tick-box texts inside a CANCELLATION document (BEAV-111).

Checkout shows two separate, unticked boxes: the acceptance of the cancellation and refund
policy, and the express request to start inside the 14-day withdrawal period with the
acknowledgement of what that costs (CRD Art. 16(a)). What the poster gives up depends on the
helper: with a private helper only TaskBeavo's fee service carries the withdrawal right; with
a trader helper the task does too. So each document carries one pair of texts per helper
type, and checkout shows the pair for the helper type the offer showed (user decision
2026-11-04, review R1-1).

The wording is legal text, so it comes from the lawyer in the CANCELLATION version in force,
per language, never from the translation catalogues (hard rule i; plan: "TaskBeavo
cancellation policy"). The publisher marks each text in the document's Markdown:

    <!-- checkout:cancellation_policy_private -->
    I accept the cancellation and refund policy.
    <!-- /checkout:cancellation_policy_private -->

    <!-- checkout:early_start_private -->
    I ask that my booking be arranged now ...
    <!-- /checkout:early_start_private -->

and the same with `_trader`. The comments don't render, so the marked sentences read as
part of the policy too. `publish` refuses a CANCELLATION version whose languages don't all
mark all four, each exactly once.
"""

import re
from dataclasses import dataclass

PRIVATE = "private"
TRADER = "trader"
VARIANTS = (PRIVATE, TRADER)
CANCELLATION_POLICY = "cancellation_policy"
EARLY_START = "early_start"
MARKERS = tuple(
    f"{text}_{variant}" for variant in VARIANTS for text in (CANCELLATION_POLICY, EARLY_START)
)

# The checkout tick boxes' markers, and the withdrawal email's (legal/withdrawal_confirmation.py).
_MARKER = re.compile(r"[ \t]*<!--\s*/?(?:checkout|email):[a-z_]+\s*-->[ \t]*\n?")


@dataclass(frozen=True)
class CheckoutConsentText:
    """One helper type's pair of tick-box texts."""

    cancellation_policy: str
    early_start: str


@dataclass(frozen=True)
class CheckoutConsentTexts:
    private: CheckoutConsentText
    trader: CheckoutConsentText

    def for_variant(self, variant: str) -> CheckoutConsentText:
        return self.trader if variant == TRADER else self.private


def variant_for(helper_is_trader: bool) -> str:
    """The tick-box variant for a helper shown as "Registered business" or not."""
    return TRADER if helper_is_trader else PRIVATE


def marked_raw(body: str, name: str, *, namespace: str = "checkout") -> str | None:
    """The text marked `<!-- namespace:name -->` … `<!-- /namespace:name -->` in `body`,
    as written; None unless it is marked exactly once."""
    found = re.findall(
        rf"<!--\s*{namespace}:{name}\s*-->(.*?)<!--\s*/{namespace}:{name}\s*-->",
        body,
        re.DOTALL,
    )
    return found[0] if len(found) == 1 else None


def marked(body: str, name: str, *, namespace: str = "checkout") -> str | None:
    """`marked_raw` with whitespace collapsed; None if it is missing or empty."""
    raw = marked_raw(body, name, namespace=namespace)
    text = " ".join(raw.split()) if raw is not None else ""
    return text or None


def _pair(body: str, variant: str) -> CheckoutConsentText | None:
    policy = marked(body, f"{CANCELLATION_POLICY}_{variant}")
    early_start = marked(body, f"{EARLY_START}_{variant}")
    if policy is None or early_start is None:
        return None
    return CheckoutConsentText(cancellation_policy=policy, early_start=early_start)


def checkout_consent_texts(body: str) -> CheckoutConsentTexts | None:
    """Both pairs marked in `body`, or None unless each of the four is marked exactly once."""
    private = _pair(body, PRIVATE)
    trader = _pair(body, TRADER)
    if private is None or trader is None:
        return None
    return CheckoutConsentTexts(private=private, trader=trader)


def without_markers(body: str) -> str:
    """`body` as readers see it: the marker comments (checkout and email) removed, their
    text kept."""
    return _MARKER.sub("", body)
