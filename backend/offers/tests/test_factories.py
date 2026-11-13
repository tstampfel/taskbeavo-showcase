"""make_helper's synthetic OIBs (BEAV-203)."""

import pytest

from core.synthetic_identifiers import SYNTHETIC_OIB_PREFIX
from offers.tests import factories


def test_helper_oibs_are_synthetic_distinct_and_restart_per_test() -> None:
    oibs = [factories._helper_oib() for _ in range(30)]
    assert len(set(oibs)) == 30
    assert all(oib.startswith(SYNTHETIC_OIB_PREFIX) for oib in oibs)
    assert factories.COMPANY_OIB not in oibs

    factories.reset_helper_oibs()
    assert factories._helper_oib() == oibs[0]


def test_a_31st_helper_in_one_test_is_refused_rather_than_reusing_an_oib() -> None:
    for _ in range(30):
        factories._helper_oib()
    with pytest.raises(RuntimeError, match="at most 30 helpers"):
        factories._helper_oib()
