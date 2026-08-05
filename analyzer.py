"""Backtest analysis and walk-forward testing."""
import pandas as pd
from typing import List, Type, Dict, Tuple
from datetime import datetime
import random

from polytester.data_layer import DataLayer
from polytester.backtest_runner import run_backtest
from polytester.strategy_interface import BaseStrategy


def calculate_metrics(deals_df: pd.DataFrame) -> Dict:
    """Calculate PF, DD, Sharpe from deal log."""
    if deals_df.empty:
        return {
            'pf': 0,
            'max_dd': 0,
            'sharpe': 0,
            'sharpe_per_trade': 0,
            'win_rate': 0,
            'n_trades': 0,
            'trades_per_year': 0,
        }

    # Profit factor
    gross_profit = deals_df[deals_df['pnl'] > 0]['pnl'].sum()
    gross_loss = abs(deals_df[deals_df['pnl'] < 0]['pnl'].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else (gross_profit / 0.01 if gross_profit > 0 else 0)

    # Win rate
    win_rate = (deals_df['pnl'] > 0).sum() / len(deals_df) if len(deals_df) > 0 else 0

    # Max drawdown
    balance = 1000  # Start with $1k
    balances = [balance]
    for _, deal in deals_df.iterrows():
        balance += deal['pnl']
        balances.append(balance)

    max_dd = 0
    running_max = balances[0]
    for bal in balances:
        running_max = max(running_max, bal)
        dd = (running_max - bal) / running_max if running_max > 0 else 0
        max_dd = max(max_dd, dd)

    # Sharpe. The per-TRADE Sharpe is assumption-free: mean/sd of per-deal PnL.
    # Annualising it needs a real trade RATE. This used to multiply by sqrt(252),
    # which silently assumes exactly one deal per trading day — badly wrong here,
    # where hundreds of markets can resolve inside a few weeks (it overstated Sharpe
    # by sqrt(actual_trades_per_year/252)). Derive the rate from the deal timestamps.
    returns = deals_df['pnl'].values
    sharpe_pt = 0.0
    if len(returns) > 1 and returns.std(ddof=1) > 0:
        sharpe_pt = returns.mean() / returns.std(ddof=1)

    trades_per_year = 0.0
    ts_col = next((c for c in ('exit_ts', 'entry_ts') if c in deals_df.columns), None)
    if ts_col is not None and len(deals_df) > 1:
        ts = pd.to_numeric(deals_df[ts_col], errors='coerce').dropna()
        span_days = (ts.max() - ts.min()) / 86400.0
        if span_days > 0:
            trades_per_year = len(ts) * 365.0 / span_days

    # Note: this treats trades as independent draws. On Polymarket they are NOT --
    # markets on the same event/day are correlated, so the effective count is lower
    # and this annualised figure is an UPPER BOUND. Measure effective breadth before
    # trusting it.
    sharpe = sharpe_pt * (trades_per_year ** 0.5) if trades_per_year > 0 else 0.0

    return {
        'pf': round(pf, 2),
        'max_dd': round(max_dd, 3),
        'sharpe': round(sharpe, 2),
        'sharpe_per_trade': round(sharpe_pt, 4),
        'win_rate': round(win_rate, 2),
        'n_trades': int(len(deals_df)),
        'trades_per_year': round(trades_per_year, 1),
    }


def walk_forward_analysis(
    strategy_class: Type[BaseStrategy],
    windows: List[Tuple[str, str, str]],  # (start, end, label)
    market_ids: List[str],
    initial_cash: float = 10000,
    n_seeds: int = 8,
) -> pd.DataFrame:
    """
    Walk-forward test: train on first window, validate on subsequent windows.

    Args:
        strategy_class: Your strategy class
        windows: List of (start_date, end_date, label) tuples
        market_ids: Markets to test (fetch from cache)
        initial_cash: Starting capital per backtest
        n_seeds: Number of random seeds per window (for RandomBaseline, this controls variance)

    Returns:
        DataFrame with columns: window, seed, pf, max_dd, sharpe, win_rate, total_trades
    """
    results = []

    for i, (start, end, label) in enumerate(windows):
        print(f"\n{label.upper()} ({start} to {end})")
        print("-" * 50)

        for seed in range(n_seeds):
            random.seed(seed)

            result = run_backtest(
                strategy_class=strategy_class,
                market_ids=market_ids,
                start=start,
                end=end,
                initial_cash=initial_cash,
            )

            deals_df = result['deals']
            metrics = calculate_metrics(deals_df)

            row = {
                'window': label,
                'seed': seed,
                'pf': metrics['pf'],
                'max_dd': metrics['max_dd'],
                'sharpe': metrics['sharpe'],
                'win_rate': metrics['win_rate'],
                'total_trades': result['total_trades'],
                'final_balance': result['final_balance'],
            }

            results.append(row)
            print(f"  Seed {seed}: PF={row['pf']}, DD={row['max_dd']}, Trades={row['total_trades']}")

    return pd.DataFrame(results)


def random_baseline_band(
    market_ids: List[str],
    start: str,
    end: str,
    n_seeds: int = 8,
    initial_cash: float = 10000,
) -> Dict:
    """
    Establish random baseline (zero-edge line).
    Run random 50/50 entry strategy across window with N seeds.
    """
    from polytester.strategy_interface import RandomBaseline

    print(f"\nEstablishing random baseline ({start} to {end})")
    print("-" * 50)

    pfs = []
    for seed in range(n_seeds):
        random.seed(seed)
        result = run_backtest(
            strategy_class=RandomBaseline,
            market_ids=market_ids,
            start=start,
            end=end,
            initial_cash=initial_cash,
        )
        metrics = calculate_metrics(result['deals'])
        pfs.append(metrics['pf'])
        print(f"  Seed {seed}: PF={metrics['pf']}")

    import numpy as np
    pf_array = np.array(pfs)

    return {
        'mean': round(pf_array.mean(), 2),
        'std': round(pf_array.std(), 2),
        'min': round(pf_array.min(), 2),
        'max': round(pf_array.max(), 2),
        'seeds': pfs,
    }
