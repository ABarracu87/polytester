# Polytester — Polymarket Backtest Harness

Event-driven backtest simulator for Polymarket prediction market strategies.

## Quick start

### 1. Test the random buyer (baseline)

```bash
python -m polytester.test_random_buyer
```

Expected output: 1 trade, settled at market resolution (YES=$1 or NO=$0).

### 2. Sync market data from Polymarket API

```bash
python -m polytester.data_layer --sync-all --interval max --fidelity 60 --max-markets 200
```

Fetches market metadata (Gamma API) and per-outcome probability history (CLOB `prices-history`). Caches in `market_db.db`. First run takes 1–2 min; subsequent calls are incremental.

### 3. Create a strategy

Subclass `BaseStrategy` and implement `on_market_state()`:

```python
from polytester.strategy_interface import BaseStrategy, Order

class MyStrategy(BaseStrategy):
    def on_market_state(self, market_state, positions):
        # market_state: {'timestamp', 'market_id', 'outcomes', 'prices', 'last_trade', 'is_open'}
        # positions: {outcome: {'size', 'entry_price', 'current_mark'}}
        #
        # NOTE: outcome names come from the market — usually 'Yes'/'No', but
        # multi-outcome markets have arbitrary names. Read them from
        # market_state['outcomes'], don't hardcode 'YES'.
        yes = market_state['outcomes'][0]
        if market_state['is_open'] and market_state['prices'][yes] > 0.6:
            return [Order(outcome=yes, side='SELL', size=100, price=None)]
        return []
```

### 4. Run a backtest

```python
from polytester.backtest_runner import run_backtest
from my_strategy import MyStrategy

result = run_backtest(
    strategy_class=MyStrategy,
    market_ids=['0x123...', '0x456...'],
    start='2026-01-01',
    end='2026-03-31',
    initial_cash=10000,
)

print(f"PF: {result['pf']}, Max DD: {result['max_dd']}, Trades: {result['total_trades']}")
result['deals'].to_csv('backtest.csv')
```

## Architecture

- **`data_layer.py`** — Fetches market metadata (Gamma) and per-outcome probability history (CLOB `prices-history`) from Polymarket API; caches to SQLite
- **`simulator.py`** — Event-replay loop: processes price bars chronologically, fills orders, marks positions, settles at resolution
- **`strategy_interface.py`** — `BaseStrategy` base class; `Order` dataclass; `RandomBaseline` reference implementation
- **`backtest_runner.py`** — Orchestrates backtests across markets, aggregates results
- **`strategies/`** — Your strategy implementations

## Key concepts

### Orders and fills

Each price bar from `prices-history` is treated as one tick (`backtest_runner._prices_to_ticks()`); there is no fill-level trade feed.

- **Market order** (`price=None`): filled at the current bar price ± modeled bid-ask spread
- **Limit order** (`price=X`): filled only when a later bar crosses your price, or at market close
- No leverage, no stop orders — positions auto-settle at market resolution

### Position settlement

At market resolution:
- Winning outcome: $1 per unit
- Losing outcome: $0 per unit
- No intermediate exit logic; buy-and-hold is the default

### Profitability metrics

- **PF (Profit Factor)** = gross profit / gross loss (>1.0 = profitable)
- **Sharpe** = risk-adjusted return (higher = better)
- **Win rate** = % of trades with PnL > 0
- **Max DD** = largest peak-to-trough equity drawdown

### Baseline

Random 50/50 entry (buy YES or NO) gives **PF ≈ 0.85–0.95** (spread drag). Anything within 10% is noise, not an edge.

## Methodology (required reading)

See [HARNESS_GUIDE.md](HARNESS_GUIDE.md) §5. Quick version:

1. **Walk-forward test** on disjoint time windows (train + 5 validation windows)
2. **Always compare to random baseline**
3. **Use ≥8 random seeds** per window
4. **Require ≥50–100 markets per window** before trusting a PF
5. **An edge is a plateau, not a spike** — good on neighboring params and multiple windows

If your strategy passes all 5 checks, it's worth watching. If it fails any, it's overfit.

## Examples

### Random buyer (test baseline)

See `strategies/random_buyer.py`. Enters 50/50 YES/NO on first tick, holds until resolution.

```bash
python -m polytester.test_random_buyer
```

### Walk-forward analysis

```python
from polytester.analyzer import walk_forward_analysis
from polytester.strategies.random_buyer import RandomBuyer

windows = [
    ('2025-02-01', '2025-04-30', 'train'),
    ('2025-05-01', '2025-06-30', 'val1'),
    ('2025-07-01', '2025-09-30', 'val2'),
]

analysis = walk_forward_analysis(
    strategy_class=RandomBuyer,
    windows=windows,
    market_ids=[...],
    n_seeds=8,
)

print(analysis)
analysis.to_csv('walk_forward.csv')
```

## Gotchas

- **Thin/short markets:** Many Polymarket markets have only a handful of price bars. An edge on thin markets is not an edge.
- **No order queue depth:** Can't infer position-in-queue for limit orders; only fill when a later price bar crosses your price.
- **Resolution lag:** UMA oracle can take 4–12 hours to finalize; don't backtest into the resolution window.
- **Spread modeling:** Default 0.5¢ per side; actual spread varies by market and time.

## Status

POC complete. Data layer handles caching; simulator replays events accurately; random baseline established. Ready for strategy development.

See [HARNESS_GUIDE.md](HARNESS_GUIDE.md) for detailed operating guide.
