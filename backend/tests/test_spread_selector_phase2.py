from app.services.spread_selector import _candidate_sort_key, select_spread_candidate


def _market(price, volume, oi, age_min=0, is_backfilled=False):
    return {
        "price": price,
        "volume": volume,
        "oi": oi,
        "age_min": age_min,
        "is_backfilled": is_backfilled,
    }


class TestSpreadSelectorPhase2:
    def test_bullish_selector_chooses_best_ranked_candidate(self):
        market = {
            (22050, "CE"): _market(82, 60_000, 700_000),
            (22100, "CE"): _market(58, 180_000, 2_100_000),
            (22150, "CE"): _market(28, 175_000, 2_000_000),
            (22200, "CE"): _market(10, 0, 0),          # invalid liquidity
            (22250, "CE"): _market(4, 0, 0),           # invalid liquidity
            (22300, "CE"): _market(2, 0, 0),           # invalid liquidity
        }

        result = select_spread_candidate(
            bias="BULLISH",
            reference_strike=22100,
            spot_price=22130,
            capital=2_500_000,
            lot_size=75,
            expiry="2026-04-16",
            option_market=market,
        )

        assert result.selected_candidate is not None
        assert result.selected_candidate["long_strike"] == 22100
        assert result.selected_candidate["short_strike"] == 22150
        assert result.selected_candidate_rank == 1
        assert result.candidate_ranking_json["valid_candidates"] >= 2

    def test_bearish_selector_chooses_best_ranked_candidate(self):
        market = {
            (22000, "PE"): _market(70, 40_000, 450_000),
            (21950, "PE"): _market(51, 130_000, 1_400_000),
            (21900, "PE"): _market(32, 165_000, 1_900_000),
            (21850, "PE"): _market(16, 160_000, 1_850_000),
            (21800, "PE"): _market(7, 0, 0),           # invalid liquidity
            (21750, "PE"): _market(2, 0, 0),           # invalid liquidity
        }

        result = select_spread_candidate(
            bias="BEARISH",
            reference_strike=21900,
            spot_price=21872,
            capital=2_500_000,
            lot_size=75,
            expiry="2026-04-16",
            option_market=market,
        )

        assert result.selected_candidate is not None
        assert result.selected_candidate["long_strike"] == 21900
        assert result.selected_candidate["short_strike"] == 21850
        assert result.selected_candidate_rank == 1
        assert result.candidate_ranking_json["signal_direction"] == "BEARISH"

    def test_sort_key_is_deterministic_for_equal_scores(self):
        higher_liquidity = {
            "score": 0.8,
            "liquidity_score": 0.9,
            "target_coverage_ratio": 2.5,
            "absolute_distance_long": 50,
            "spread_debit": 28,
            "long_strike": 22100,
        }
        lower_liquidity = {
            "score": 0.8,
            "liquidity_score": 0.7,
            "target_coverage_ratio": 2.5,
            "absolute_distance_long": 50,
            "spread_debit": 28,
            "long_strike": 22150,
        }

        ordered = sorted([lower_liquidity, higher_liquidity], key=_candidate_sort_key)
        assert ordered[0]["liquidity_score"] == 0.9

    def test_stale_candidate_is_rejected_before_ranking(self):
        market = {
            (22050, "CE"): _market(82, 60_000, 700_000),
            (22100, "CE"): _market(58, 180_000, 2_100_000, age_min=6, is_backfilled=True),
            (22150, "CE"): _market(28, 175_000, 2_000_000, age_min=6, is_backfilled=True),
            (22200, "CE"): _market(10, 0, 0),
            (22250, "CE"): _market(4, 0, 0),
            (22300, "CE"): _market(2, 0, 0),
        }

        result = select_spread_candidate(
            bias="BULLISH",
            reference_strike=22100,
            spot_price=22130,
            capital=2_500_000,
            lot_size=75,
            expiry="2026-04-16",
            option_market=market,
        )

        assert result.selected_candidate is None
        assert result.reason_code == "NO_VALID_CANDIDATE_AFTER_RANKING"
        stale_candidate = next(
            c for c in result.candidate_ranking_json["candidates"]
            if c["long_strike"] == 22100 and c["short_strike"] == 22150
        )
        assert stale_candidate["rejection_reason"] == "STALE_OPTION_PRICE"

    def test_zero_volume_or_oi_candidate_is_rejected(self):
        market = {
            (22050, "CE"): _market(82, 0, 700_000),
            (22100, "CE"): _market(58, 0, 2_100_000),
            (22150, "CE"): _market(28, 0, 0),
            (22200, "CE"): _market(10, 0, 0),
            (22250, "CE"): _market(4, 0, 0),
            (22300, "CE"): _market(2, 0, 0),
        }

        result = select_spread_candidate(
            bias="BULLISH",
            reference_strike=22100,
            spot_price=22130,
            capital=2_500_000,
            lot_size=75,
            expiry="2026-04-16",
            option_market=market,
        )

        assert result.selected_candidate is None
        assert result.reason_code == "LOW_LIQUIDITY_REJECT"
        priced_candidates = [
            c for c in result.candidate_ranking_json["candidates"]
            if c["has_usable_prices"]
        ]
        assert priced_candidates
        assert all(c["rejection_reason"] == "LOW_LIQUIDITY_REJECT" for c in priced_candidates)

    def test_within_threshold_backfill_is_penalized_not_rejected(self):
        market = {
            (22000, "CE"): _market(90, 10_000, 120_000, age_min=1, is_backfilled=True),
            (22050, "CE"): _market(55, 10_000, 120_000, age_min=1, is_backfilled=True),
            (22100, "CE"): _market(58, 180_000, 2_100_000, age_min=1, is_backfilled=True),
            (22150, "CE"): _market(28, 175_000, 2_000_000, age_min=1, is_backfilled=True),
            (22200, "CE"): _market(10, 0, 0),
            (22250, "CE"): _market(4, 0, 0),
            (22300, "CE"): _market(2, 0, 0),
        }

        result = select_spread_candidate(
            bias="BULLISH",
            reference_strike=22100,
            spot_price=22130,
            capital=2_500_000,
            lot_size=75,
            expiry="2026-04-16",
            option_market=market,
        )

        assert result.selected_candidate is not None
        selected = result.selected_candidate
        assert selected["long_leg_age_min"] == 1
        assert selected["short_leg_age_min"] == 1
        assert selected["freshness_score"] < 1.0
        assert selected["rejection_reason"] is None
