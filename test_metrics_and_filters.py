"""Self-checks for the 2026-08-05 changes: Sharpe annualisation, real-spread cost,
and the market-filter plumbing. Assert-based, no framework.

Run: python -m polytester.test_metrics_and_filters
"""
import pandas as pd

from polytester.analyzer import calculate_metrics
from polytester.data_layer import DataLayer


def _deals(pnls, start_ts=1_700_000_000, step=86400):
    """One deal per `step` seconds."""
    return pd.DataFrame([
        {"outcome": "Yes", "side": "BUY", "entry_price": 0.5, "exit_price": 1.0,
         "size": 100, "pnl": p, "entry_ts": start_ts + i * step,
         "exit_ts": start_ts + i * step, "exit_reason": "settle"}
        for i, p in enumerate(pnls)
    ])


def test_sharpe_annualisation_uses_real_rate():
    """The old code multiplied per-trade Sharpe by sqrt(252) regardless of the
    actual trade rate. Daily deals must still give ~sqrt(365) scaling, and deals
    packed 10x denser must scale up, not stay put."""
    pnls = [10, -5, 8, -3, 12, -6, 9, -4, 11, -5] * 4

    daily = calculate_metrics(_deals(pnls, step=86400))
    dense = calculate_metrics(_deals(pnls, step=8640))  # 10x more often

    # per-trade Sharpe is a property of the PnL distribution only -> identical
    assert daily["sharpe_per_trade"] == dense["sharpe_per_trade"], (
        daily["sharpe_per_trade"], dense["sharpe_per_trade"])
    assert daily["sharpe_per_trade"] > 0

    # 10x the rate => sqrt(10) ~ 3.16x the annualised Sharpe
    ratio = dense["sharpe"] / daily["sharpe"]
    assert 3.0 < ratio < 3.3, f"expected ~sqrt(10) scaling, got {ratio:.3f}"

    # daily deals -> a rate near 365/yr (n deals span n-1 days, so slightly above)
    assert 360 < daily["trades_per_year"] < 385, daily["trades_per_year"]
    # annualised must equal per-trade * sqrt(rate), using the RATE THE FUNCTION
    # REPORTS -- deriving it from a nominal 365 fails on rounding alone
    expect = daily["sharpe_per_trade"] * daily["trades_per_year"] ** 0.5
    assert abs(daily["sharpe"] - expect) < 0.02, (daily["sharpe"], expect)


def test_metrics_degenerate_cases():
    empty = calculate_metrics(pd.DataFrame())
    assert empty["sharpe"] == 0 and empty["n_trades"] == 0

    one = calculate_metrics(_deals([5]))
    assert one["sharpe"] == 0, "a single trade has no dispersion -> no Sharpe"
    assert one["n_trades"] == 1

    # all-winners: no gross loss, PF must not blow up or divide by zero
    wins = calculate_metrics(_deals([1, 2, 3]))
    assert wins["pf"] > 0 and wins["win_rate"] == 1.0


def test_pf_and_drawdown_signs():
    m = calculate_metrics(_deals([100, -50]))
    assert abs(m["pf"] - 2.0) < 1e-9, m["pf"]      # 100 profit / 50 loss
    assert m["max_dd"] > 0, "a losing deal after a winner must show drawdown"
    assert m["win_rate"] == 0.5


def test_schema_has_new_columns():
    """spread / sports_type / volume must exist, including on a pre-existing cache
    (they are added by in-place migration, not a fresh CREATE TABLE)."""
    dl = DataLayer()
    cols = {r[1] for r in dl.db.execute("PRAGMA table_info(markets)")}
    for c in ("spread", "sports_type", "volume"):
        assert c in cols, f"missing migrated column {c}"


def test_fetch_markets_signature_defaults():
    """order must default to total volume, not volume24hr -- the latter caps the
    reachable history at weeks."""
    import inspect
    sig = inspect.signature(DataLayer.fetch_markets)
    assert sig.parameters["order"].default == "volume", "order default regressed"
    assert "tag_id" in sig.parameters
    assert sig.parameters["exclude_sports"].default is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("\nAll self-checks passed")
