"""Golden-master: pin the engine's *decisions*, not just its P&L.

Every bug this project hit in September was silent -- a hedge that never armed,
a run re-created under an existing id, lots falling back to 1. None of them
raised; they just quietly produced different trades. A P&L assertion can miss
that (two different books can net the same), so these fingerprints capture the
whole decision stream: which legs were opened at which strike and size, and the
ordered list of events with their timestamps.

Any change to engine behaviour breaks these. That is the point. When a change is
intended, run the test, copy the printed fingerprint's digest into GOLDEN, and
make the update a visible line in the diff that a reviewer has to agree with.

No database and no market data: the loaders are patched, so this runs anywhere.
"""
import hashlib
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

import app.services.straddle_adjustment_executor as executor
from app.models.strategy_run import StrategyRun, StrategyRunEvent, StrategyRunLeg
from app.services.generic_executor import ValidationResult

D = (2026, 5, 6)


class _FakeDb:
    def __init__(self):
        self.added, self.commits, self.rollbacks = [], 0, 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _validation(**over):
    v = {"validated": True, "instrument": "NIFTY", "trade_date": "2026-05-06",
         "entry_time": "09:50", "resolved_expiry": "2026-05-12",
         "spot_at_entry": 22500.0, "atm_strike": 22500, "contracts": [],
         "lot_size": 75, "approved_lots": 1, "estimated_margin": 250000, "warnings": []}
    v.update(over)
    return ValidationResult(**v)


def _strategy(**exit_over):
    ex = {"time_exit": "15:25", "stop_capital_pct": 0.2, "trail_trigger": 0,
          "trail_pct": 0, "lock_trigger": 1000, "loss_lock_trigger": 1000,
          "wing_width_steps": 2}
    ex.update(exit_over)
    return {"id": "short_straddle_dual_lock", "version": "v1",
            "executor": "straddle_adjustment_v1", "entry_rule_id": "timed_entry",
            "exit_rule": ex}


def _config(**over):
    c = {"run_type": "single_session_backtest", "instrument": "NIFTY",
         "trade_date": "2026-05-06", "entry_time": "09:50", "capital": 2500000}
    c.update(over)
    return c


def _spot(*rows):
    return [{"date": datetime(*D, h, m), "close": c} for h, m, c in rows]


def _index(rows):
    base = datetime(*D, 9, 15)
    out = {}
    for strike, ot, h, m, price in rows:
        idx = int((datetime(*D, h, m) - base).total_seconds() / 60)
        out.setdefault((strike, ot), {})[idx] = {"price": price}
    return out


async def _patch(monkeypatch, spot_rows, option_index):
    async def contract_spec(_db, _i, _d):
        return SimpleNamespace(strike_step=50)

    async def option_loader(_db, _i, _d, _e, strike_keys, option_price_source="close"):
        return option_index, []

    async def spot_loader(_db, _i, _d):
        return spot_rows

    async def vix_loader(_db, _d):
        return [{"date": r["date"], "vix_close": 12.5} for r in spot_rows]

    monkeypatch.setattr(executor, "get_contract_spec", contract_spec)
    monkeypatch.setattr(executor, "load_option_candles_for_strikes", option_loader)
    monkeypatch.setattr(executor, "load_spot_candles", spot_loader)
    monkeypatch.setattr(executor, "load_vix_candles", vix_loader)
    monkeypatch.setattr(executor, "vix_at_time", lambda rows, ts: 12.5)


def _fingerprint(db, result):
    """Canonical, ordered record of what the engine decided."""
    lines = [f"status={result.status} exit={result.exit_reason}"]
    run = next((o for o in db.added if isinstance(o, StrategyRun)), None)
    if run is not None:
        lines.append(
            f"run gross={_r(run.gross_pnl)} charges={_r(run.total_charges)} "
            f"net={_r(run.realized_net_pnl)} lots={run.approved_lots} "
            f"credit={_r(run.entry_credit_per_unit)} "
            f"locked={(run.result_json or {}).get('wings_locked')} "
            f"reason={(run.result_json or {}).get('lock_reason')}"
        )
    for l in sorted((o for o in db.added if isinstance(o, StrategyRunLeg)),
                    key=lambda x: x.leg_index):
        lines.append(f"leg {l.leg_index} {l.side} {l.option_type} {l.strike} "
                     f"qty={l.quantity} in={_r(l.entry_price)} out={_r(l.exit_price)}")
    for e in sorted((o for o in db.added if isinstance(o, StrategyRunEvent)),
                    key=lambda x: (x.timestamp, x.event_type, x.reason_code or "")):
        lines.append(f"event {x_t(e.timestamp)} {e.event_type} {e.reason_code or '-'}")
    return "\n".join(lines)


def _r(v):
    return "None" if v is None else f"{float(v):.2f}"


def x_t(ts):
    return ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(ts)


# ── scenarios ────────────────────────────────────────────────────────────────
QUIET = dict(
    spot=_spot((9, 50, 22500), (9, 51, 22505), (15, 25, 22508)),
    opts=_index([
        (22500, "CE", 9, 50, 100), (22500, "PE", 9, 50, 100),
        (22600, "CE", 9, 50, 10),  (22400, "PE", 9, 50, 10),
        (22500, "CE", 9, 51, 99),  (22500, "PE", 9, 51, 99),
        (22600, "CE", 9, 51, 10),  (22400, "PE", 9, 51, 10),
        (22500, "CE", 15, 25, 95), (22500, "PE", 15, 25, 96),
        (22600, "CE", 15, 25, 9),  (22400, "PE", 15, 25, 9),
    ]),
    exit_rule={"lock_trigger": 1_000_000, "loss_lock_trigger": 1_000_000},
)

PROFIT_LOCK = dict(
    spot=_spot((9, 50, 22500), (9, 51, 22510), (15, 25, 22525)),
    opts=_index([
        (22500, "CE", 9, 50, 100), (22500, "PE", 9, 50, 100),
        (22600, "CE", 9, 50, 10),  (22400, "PE", 9, 50, 10),
        (22500, "CE", 9, 51, 50),  (22500, "PE", 9, 51, 50),
        (22600, "CE", 9, 51, 12),  (22400, "PE", 9, 51, 12),
        (22500, "CE", 15, 25, 40), (22500, "PE", 15, 25, 45),
        (22600, "CE", 15, 25, 15), (22400, "PE", 15, 25, 14),
    ]),
    exit_rule={},
)

ADVERSE = dict(
    spot=_spot((9, 50, 22500), (9, 51, 22700), (15, 25, 22750)),
    opts=_index([
        (22500, "CE", 9, 50, 100), (22500, "PE", 9, 50, 100),
        (22600, "CE", 9, 50, 10),  (22400, "PE", 9, 50, 10),
        (22500, "CE", 9, 51, 260), (22500, "PE", 9, 51, 20),
        (22600, "CE", 9, 51, 180), (22400, "PE", 9, 51, 8),
        (22500, "CE", 15, 25, 300),(22500, "PE", 15, 25, 5),
        (22600, "CE", 15, 25, 210),(22400, "PE", 15, 25, 3),
    ]),
    # 0.2% of 25L = a 5,000 stop, which the 09:51 mark breaches -- without this
    # the scenario only pinned a losing day, never the STOP_EXIT branch.
    exit_rule={"lock_trigger": 1_000_000, "loss_lock_trigger": 1_000_000,
               "stop_capital_pct": 0.002},
)

# The path that actually broke in September: 13 lots so net delta can clear the
# 150 threshold, and a 200-point move to push it there.
DELTA_HEDGE = dict(
    spot=_spot((9, 50, 22500), (9, 51, 22700), (15, 25, 22690)),
    opts=_index([
        (22500, "CE", 9, 50, 100), (22500, "PE", 9, 50, 100),
        (22600, "CE", 9, 50, 55),  (22400, "PE", 9, 50, 55),
        (22500, "CE", 9, 51, 250), (22500, "PE", 9, 51, 30),
        (22600, "CE", 9, 51, 175), (22400, "PE", 9, 51, 18),
        (22500, "CE", 15, 25, 195),(22500, "PE", 15, 25, 12),
        (22600, "CE", 15, 25, 100),(22400, "PE", 15, 25, 6),
    ]),
    exit_rule={"lock_trigger": 1_000_000, "loss_lock_trigger": 1_000_000,
               "stop_capital_pct": 0.5},
    validation={"approved_lots": 13},
    config={"delta_hedge_enabled": True, "delta_threshold": 150,
            "hedge_action": "BUY_WING", "hedge_qty_mode": "PARTIAL",
            "max_hedge_triggers": 5, "reentry_buffer": 50, "default_iv": 0.12},
)

SCENARIOS = {"quiet": QUIET, "profit_lock": PROFIT_LOCK,
             "adverse": ADVERSE, "delta_hedge": DELTA_HEDGE}

# Regenerate deliberately: run the test, copy the printed digest, and let the
# diff show a reviewer exactly which behaviour changed.
GOLDEN = {
    "quiet":       "fb06a49abc0c67603893fa3178e9ff841f138a9d3b366312962091580b3d59ce",
    "profit_lock": "2ea4b4b86b3d86708e7f79fa47058feedd5c88ac639e6249b99feee22b6983fc",
    "adverse":     "11f29d004f350861d6d3d69149f0e3873ed3d85c0455cb7cf398727fd7de16da",
    "delta_hedge": "f36a79feb5ebe72136c2640a1e530191467180df081e3835bad458692fed6036",
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.asyncio
async def test_engine_decisions_are_unchanged(name, monkeypatch):
    sc = SCENARIOS[name]
    db = _FakeDb()
    await _patch(monkeypatch, sc["spot"], sc["opts"])
    result = await executor.execute_run(
        db, uuid.uuid4(), _strategy(**sc["exit_rule"]),
        _config(**sc.get("config", {})), _validation(**sc.get("validation", {})))
    fp = _fingerprint(db, result)
    digest = hashlib.sha256(fp.encode()).hexdigest()
    assert digest == GOLDEN[name], (
        f"\n\nEngine behaviour changed for scenario '{name}'.\n"
        f"If that was intended, set GOLDEN['{name}'] = '{digest}'\n"
        f"and make sure the diff below is what you meant:\n\n{fp}\n"
    )
