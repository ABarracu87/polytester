# Polytester Setup and First Run

## Installation

```bash
git clone https://github.com/ABarracu87/polytester.git
cd polytester
pip install -e .
```

Installs polytester + dependencies (pandas, requests).

## First run: test the harness

```bash
python -m polytester.test_random_buyer
```

Output:
```
Random Buyer Test Results
========================================
Total trades: 1
Final balance: $1099.00 (or $899.00 if random bought YES vs NO)
PF: 0
Win rate: 1.0

Deals:
  outcome side  entry_price  exit_price   size   pnl  entry_ts   exit_ts
0     YES  BUY        0.505         1.0  200.0  99.0  1704067100  1704067200

✓ Test passed
```

This confirms:
- ✓ Strategy loads and runs
- ✓ Orders are filled at price bars
- ✓ Positions settle at market resolution
- ✓ PnL is calculated correctly

## Next: sync real data

Polymarket API endpoints (verified live 2026-07-26):
- Gamma API (`https://gamma-api.polymarket.com/markets`): market metadata
- CLOB API (`https://clob.polymarket.com/prices-history`): per-outcome probability history

```bash
python -m polytester.data_layer --sync-all --interval max --fidelity 60 --max-markets 200
```

This fetches and caches:
- Market list (`market_db.db`)
- Per-outcome probability history (bar size set by `--fidelity`, in minutes)

Takes ~1–2 min first run, then incremental. Cache is local.

## Build a strategy

Create `strategies/my_strategy.py`:

```python
from polytester.strategy_interface import BaseStrategy, Order

class MyStrategy(BaseStrategy):
    def on_market_state(self, market_state, positions):
        # Your logic here
        # Return list of Order objects (can be empty)
        return []
```

Test it:

```python
from polytester.backtest_runner import run_backtest
from polytester.strategies.my_strategy import MyStrategy

result = run_backtest(
    strategy_class=MyStrategy,
    market_ids=[...],  # from cache
    start='2026-01-01',
    end='2026-03-31',
)

print(f"PF: {result['pf']}, Trades: {result['total_trades']}")
```

## File structure

```
polytester/
├── __init__.py
├── HARNESS_GUIDE.md         ← Detailed operating guide
├── README.md                ← Quick start
├── SETUP.md                 ← This file
├── data_layer.py            ← API fetching + caching
├── simulator.py             ← Event-replay engine
├── strategy_interface.py    ← BaseStrategy + Order
├── backtest_runner.py       ← Orchestration
├── test_random_buyer.py     ← Test harness (run this first)
├── strategies/
│   ├── __init__.py
│   ├── random_buyer.py      ← Baseline reference (hold to resolution)
│   └── price_trader.py      ← Mid-market exit example (TP/SL on price)
└── market_db.db             ← SQLite cache (created on first sync)
```

## Troubleshooting

**ModuleNotFoundError: No module named 'polytester'**
→ Run from workspace root: `python -m polytester.test_random_buyer` (not `cd polytester && python test_random_buyer.py`)

**No trades in backtest**
→ Check cache: `python -m polytester.data_layer --market-list`. If empty, run `--sync-all` first.

**API connection errors**
→ Polymarket endpoints may have changed. Check [polymarket.com](https://polymarket.com) for latest API docs.

## Validation checklist

- [ ] `python -m polytester.test_random_buyer` passes
- [ ] `python -m polytester.data_layer --market-list` shows ≥1 market
- [ ] Can create a strategy subclass without syntax errors
- [ ] `run_backtest()` returns a dict with 'pf', 'deals', etc.

Once all boxes are checked, you're ready to build and test strategies.
