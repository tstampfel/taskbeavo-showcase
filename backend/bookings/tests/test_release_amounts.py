"""The partial release's amounts (BEAV-117; bookings/cancellation.py,
`partial_release_outcome`): pure functions, every cent accounted for."""

import pytest

from bookings.cancellation import Case, Outcome, partial_release_outcome


def _money_after(outcome: Outcome) -> tuple[int, int, int]:
    """Who ends with what once the Stripe steps ran, from the destination charge's start:
    the helper's account holds P, TaskBeavo F."""
    helper = outcome.amount_cents + outcome.fee_refund_cents - outcome.transfer_reversal_cents
    taskbeavo = (
        outcome.fee_cents
        - outcome.fee_refund_cents
        + outcome.transfer_reversal_cents
        - outcome.refund_cents
    )
    return helper, taskbeavo, outcome.refund_cents


@pytest.mark.parametrize(
    ("amount", "fee", "released", "refund", "kept", "reversal", "fee_refund"),
    [
        # EUR 50 task, fee 6.00; the poster releases 40: K = 4.80, refund 10 + 1.20.
        (5000, 600, 4000, 1120, 480, 1120, 120),
        # EUR 10 task at the 2.50 minimum fee; releases 5: K = 0.60, refund 6.90.
        (1000, 250, 500, 690, 60, 690, 190),
        # A credit covered the whole fee (F = 0): K comes back from the helper too.
        (5000, 0, 4000, 520, 480, 1000, 0),
        # One cent: K rounds to 0.
        (5000, 600, 1, 5599, 0, 5599, 600),
        # One cent under the price.
        (5000, 600, 4999, 1, 600, 1, 0),
    ],
)
def test_a_partial_release_splits_the_charge_to_the_cent(
    amount: int, fee: int, released: int, refund: int, kept: int, reversal: int, fee_refund: int
) -> None:
    outcome = partial_release_outcome(
        amount_cents=amount, fee_cents=fee, fee_percent=12, released_cents=released
    )

    assert outcome.case == Case.PARTIAL_RELEASE
    assert outcome.helper_compensation_cents == released
    assert outcome.kept_fee_cents == kept
    assert outcome.refund_cents == refund
    assert outcome.transfer_reversal_cents == reversal
    assert outcome.fee_refund_cents == fee_refund
    assert outcome.poster_credit_cents == 0
    assert not outcome.is_full_refund
    # Reconciles: the helper ends with C, TaskBeavo with K, the poster with R.
    assert _money_after(outcome) == (released, kept, refund)
    assert released + kept + refund == amount + fee


def test_k_uses_the_bookings_own_rate() -> None:
    outcome = partial_release_outcome(
        amount_cents=5000, fee_cents=500, fee_percent=10, released_cents=4000
    )

    assert outcome.kept_fee_cents == 400
    assert outcome.refund_cents == 5500 - 4000 - 400


@pytest.mark.parametrize("released", [0, -1, 5000, 5001, True, 12.5])
def test_a_partial_release_is_more_than_nothing_and_less_than_everything(
    released: object,
) -> None:
    with pytest.raises(ValueError):
        partial_release_outcome(
            amount_cents=5000,
            fee_cents=600,
            fee_percent=12,
            released_cents=released,  # pyright: ignore[reportArgumentType]
        )


def test_a_stored_partial_release_is_checked_again() -> None:
    good = partial_release_outcome(
        amount_cents=5000, fee_cents=600, fee_percent=12, released_cents=4000
    )
    fields = {**good.__dict__, "refund_cents": good.refund_cents + 1}

    with pytest.raises(ValueError):
        Outcome(**fields)
