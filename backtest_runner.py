"""Orchestrate backtests across markets and windows."""
import json
from datetime import datetime
from typing import List, Type, Dict, Optional
import pandas as pd

from polytester.data_layer import DataLayer
from polytester.simulator import Simulator
from polytester.strategy_interface import BaseStrategy


def _prices_to_ticks(price_rows: List[Dict]) -> List[Dict]:
    """Convert cached price-history rows into the tick stream the simulator
    consumes. Each price bar = one 'trade print' at that outcome's probability.

    Polymarket's free historical data is the probability time series, not
    fill-level trades, so a bar IS our tick. Size is unknown -> set to 1 (the
    simulator uses last-trade price for fills/marks, not size). This is the
    prices-history fill model: honest on price, blind to depth/queue.
    """
    ticks = [
        {"outcome": r["outcome"], "price": r["price"], "size": 1, "timestamp": r["timestamp"]}
        for r in price_rows
    ]
    ticks.sort(key=lambda t: t["timestamp"])
    return ticks


def run_backtest(
    strategy_class: Type[BaseStrategy],
    market_ids: List[str],
    start: str,
    end: str,
    initial_cash: float = 10000,
    timeout_sec: float = 300,
    use_real_spread: bool = False,
    bid_ask_cents: float = 0.5,
) -> Dict:
    """
    Run backtest across multiple markets.

    Returns:
        {
            'pf': float,
            'max_dd': float,
            'sharpe': float,
            'total_trades': int,
            'win_rate': float,
            'final_balance': float,
            'deals': DataFrame,
        }
    """
    dl = DataLayer()
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)

    all_deals = []
    total_pnl = 0

    for market_id in market_ids:
        market_meta = dl.get_market(market_id)
        if not market_meta:
            continue

        # Only backtest resolved markets — an unresolved market has no known
        # settlement, so PnL is undefined.
        if not market_meta['resolved_outcome']:
            continue

        price_rows = dl.get_prices(market_id, start_dt, end_dt)
        ticks = _prices_to_ticks(price_rows)
        if not ticks:
            continue

        strategy = strategy_class(market_id, initial_cash)

        # Cost model. The default 0.5c/side is a fixed guess; Gamma reports each
        # market's own quoted spread (median 0.0010 = 0.1c, i.e. 5x tighter), so
        # use_real_spread=True is the honest setting where that field is populated.
        # Gamma's `spread` is the FULL bid-ask, and the simulator applies bid_ask as
        # a per-side slip, so halve it.
        cents = bid_ask_cents
        if use_real_spread:
            raw = market_meta.get('spread') if isinstance(market_meta, dict) else None
            if raw is not None and 0 < float(raw) < 1:
                cents = float(raw) * 100 / 2

        sim = Simulator(strategy, market_id, initial_cash, bid_ask_cents=cents)

        outcomes = json.loads(market_meta['outcomes'] or '[]')

        sim.run(
            ticks,
            {
                'outcomes': outcomes or ['Yes', 'No'],
                'resolved_outcome': market_meta['resolved_outcome'],
                'end_time': market_meta['end_time'],
            },
            market_meta['end_time']
        )

        results = sim.get_results()
        all_deals.extend(results['deals'].to_dict('records'))
        total_pnl += (results['final_balance'] - initial_cash)

    # Aggregate. Metrics come from analyzer.calculate_metrics so there is ONE
    # implementation; max_dd and sharpe used to be hardcoded 0 here, which silently
    # made any result read through this function unscoreable.
    # Local import: analyzer imports run_backtest, so a module-level import cycles.
    from polytester.analyzer import calculate_metrics

    deals_df = pd.DataFrame(all_deals) if all_deals else pd.DataFrame()
    metrics = calculate_metrics(deals_df)

    return {
        'pf': metrics['pf'],
        'max_dd': metrics['max_dd'],
        'sharpe': metrics['sharpe'],
        'sharpe_per_trade': metrics['sharpe_per_trade'],
        'total_trades': len(deals_df),
        'win_rate': metrics['win_rate'],
        'final_balance': initial_cash + total_pnl,
        'deals': deals_df,
    }


if __name__ == "__main__":
    from polytester.strategy_interface import RandomBaseline

    # Demo: random baseline on all resolved markets in the cache
    dl = DataLayer()
    resolved = dl.list_markets(resolved_only=True)
    market_ids = [m['market_id'] for m in resolved]
    print(f"Backtesting {len(market_ids)} resolved markets from cache")

    result = run_backtest(
        strategy_class=RandomBaseline,
        market_ids=market_ids,
        start='2020-01-01',   # wide window; cache holds only what exists
        end='2030-01-01',
        initial_cash=10000,
    )

    print(f"PF: {result['pf']}")
    print(f"Trades: {result['total_trades']}")
    print(f"Win rate: {result['win_rate']}")
    print(f"Final balance: ${result['final_balance']:.2f}")

    if not result['deals'].empty:
        result['deals'].to_csv('/tmp/backtest_demo.csv', index=False)
        print("Deals exported to /tmp/backtest_demo.csv")
