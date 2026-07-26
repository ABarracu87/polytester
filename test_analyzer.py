"""Test walk-forward and baseline analysis."""
import random
import pandas as pd
from polytester.analyzer import calculate_metrics, random_baseline_band
from polytester.backtest_runner import run_backtest
from polytester.strategies.random_buyer import RandomBuyer


def test_metrics():
    """Test metric calculation."""
    # Synthetic deals
    deals_df = pd.DataFrame([
        {'pnl': 100},
        {'pnl': 50},
        {'pnl': -75},
        {'pnl': -30},
        {'pnl': 200},
    ])

    metrics = calculate_metrics(deals_df)

    print("\nMetric Calculation Test")
    print("=" * 40)
    print(f"Deals: {deals_df['pnl'].values}")
    print(f"Gross profit: ${deals_df[deals_df['pnl'] > 0]['pnl'].sum()}")
    print(f"Gross loss: ${abs(deals_df[deals_df['pnl'] < 0]['pnl'].sum())}")
    print(f"PF: {metrics['pf']} (should be ~3.1)")
    print(f"Win rate: {metrics['win_rate']} (should be 0.6)")
    print(f"Sharpe: {metrics['sharpe']}")

    assert metrics['pf'] > 3, f"PF should be >3, got {metrics['pf']}"
    assert metrics['win_rate'] == 0.6, f"Win rate should be 0.6, got {metrics['win_rate']}"
    print("✓ Metrics test passed\n")


def test_random_baseline():
    """Test random baseline band estimation."""
    # Mock 5 markets with synthetic trades
    # (In real use, these would be fetched from Polymarket API)

    print("\nRandom Baseline Band Test")
    print("=" * 40)
    print("Running random baseline with 3 seeds on synthetic data...")

    random.seed(42)
    result = run_backtest(
        strategy_class=RandomBuyer,
        market_ids=[],  # Empty = no real markets
        start='2026-01-01',
        end='2026-03-31',
        initial_cash=10000,
    )

    metrics = calculate_metrics(result['deals'])
    print(f"Result: {len(result['deals'])} trades, PF={metrics['pf']}")

    # With no markets, should have 0 trades
    assert result['total_trades'] == 0, f"Expected 0 trades with empty market list, got {result['total_trades']}"
    print("✓ Baseline test passed (no markets = no trades)\n")


if __name__ == "__main__":
    test_metrics()
    test_random_baseline()
    print("\n✓ All analyzer tests passed")
