"""ATM snapping: the optional NEAREST_100 rule for the A/B slot.

The straddle is normally sold at the nearest 50. NEAREST_100 coarsens that to
round hundreds with **ties going down**, which is asymmetric on purpose: it is
the rule the slot config describes, and plain rounding would not deliver it.
Python rounds halves to even, so round(232.5)=232 but round(233.5)=234 -- the
midpoint would fall down at 23,250 and up at 23,350.
"""
import pytest

from app.services.contract_spec_service import resolve_atm_strike, snaps_to_100


def test_default_is_unchanged_by_the_new_argument():
    """Existing slots must resolve exactly as before."""
    for spot in (23_249, 23_250, 23_251, 23_274, 23_275, 23_276, 24_012.4):
        assert resolve_atm_strike(spot, 50) == resolve_atm_strike(spot, 50, None)


@pytest.mark.parametrize("spot,expected", [
    (23_249, 23_200),
    (23_250, 23_200),   # tie goes down
    (23_251, 23_300),
    (23_349, 23_300),
    (23_350, 23_300),   # tie goes down again -- consistently, unlike round()
    (23_351, 23_400),
])
def test_nearest_100_rounds_with_ties_down(spot, expected):
    assert resolve_atm_strike(spot, 50, "NEAREST_100") == expected


def test_ties_are_consistent_where_plain_rounding_is_not():
    """round() would send 23,250 down and 23,350 up. Both must go down."""
    assert resolve_atm_strike(23_250, 50, "NEAREST_100") == 23_200
    assert resolve_atm_strike(23_350, 50, "NEAREST_100") == 23_300
    assert round(232.5) * 100 == 23_200 and round(233.5) * 100 == 23_400


def test_exact_hundreds_stay_put():
    for k in (23_100, 23_200, 23_300, 24_000):
        assert resolve_atm_strike(k, 50, "NEAREST_100") == k


def test_fractional_spot_just_above_a_tie_rounds_up():
    assert resolve_atm_strike(23_250.05, 50, "NEAREST_100") == 23_300
    assert resolve_atm_strike(23_249.95, 50, "NEAREST_100") == 23_200


def test_unknown_or_default_snap_falls_back_to_the_step():
    assert resolve_atm_strike(23_249, 50, "NEAREST_50") == 23_250
    assert resolve_atm_strike(23_249, 50, "") == 23_250
    assert resolve_atm_strike(23_249, 50, "something_else") == 23_250


def test_snap_is_case_insensitive():
    assert resolve_atm_strike(23_251, 50, "nearest_100") == 23_300


def test_wings_stay_on_hundreds_when_atm_does():
    """Wings derive from ATM +/- wing_steps * strike_step, so a 100-snapped ATM
    keeps the whole structure on round hundreds."""
    atm = resolve_atm_strike(23_251, 50, "NEAREST_100")
    assert (atm + 2 * 50) % 100 == 0
    assert (atm - 2 * 50) % 100 == 0


# ── snaps_to_100: the opt-in predicate both engines gate on ───────────────────
# It exists so no caller tests the raw param for truthiness. NEAREST_50 is a
# non-empty string, so truthiness would treat a normally-configured slot as
# opting in -- which is how the live resolver briefly started moving every
# slot's spot source off 09:49.

@pytest.mark.parametrize("value", ["NEAREST_100", "nearest_100", "Nearest_100", " NEAREST_100 "])
def test_snaps_to_100_accepts_the_opt_in_normalized(value):
    assert snaps_to_100(value) is True


@pytest.mark.parametrize("value", [
    None, "", "   ", "NEAREST_50", "nearest_50", "NEAREST_200", "100", "true", "NEAREST100",
])
def test_snaps_to_100_rejects_everything_else(value):
    assert snaps_to_100(value) is False, f"{value!r} must not opt a slot into 100-snapping"


def test_predicate_and_resolver_agree():
    """The resolver is defined in terms of the predicate, so they cannot drift."""
    for value in [None, "", "NEAREST_50", "nearest_100", "NEAREST_100", "WAT"]:
        coarsened = resolve_atm_strike(23_260.0, 50, value) == 23_300
        assert coarsened is snaps_to_100(value), value
