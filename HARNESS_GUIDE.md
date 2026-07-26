# Polymarket Backtest Harness — Operating Guide

Event-replay simulator for Polymarket CLOB strategies. Tests directional (YES/NO) and cross-outcome strategies against historical data with honest fill modeling and market-resolution settlement.

---

## 0. Architecture

**Components:**
- **`data_layer.py`** — Fetches and caches market metadata, probability history, and trade logs from Polymarket API
- **`simulator.py`** — Event-driven backtest engine: processes trades chronologically, fills orders, marks positions, settles at resolution
- **`strategy_interface.py`** — Base class for strategies; you subclass and implement `on_market_state()`
- **`analyzer.py`** — Computes PF, drawdown, Sharpe, win rate per market and aggregated across windows
- **`backtest_runner.py`** — Orchestrates data→strategy→settlement→reporting, handles walk-forward windows

**Data flow:**
```
Polymarket API (Gamma, CLOB, Data)
  ↓
SQLite cache (market_db.db)
  ↓
Simulator (time-series replay)
  ↓
Trade log (CSV)
  ↓
Analyzer (PF, DD, Sharpe)
```

---

## 1. Data layer — fetching and caching

**Verified live 2026-07-26.** Three endpoint quirks were load-bearing and are
baked into `data_layer.py`; read them before touching the fetch code.

### The three gotchas that cost debug cycles

1. **Gamma list fields are JSON-encoded STRINGS.** `outcomes`,
   `outcomePrices`, and `clobTokenIds` come back as `'["Yes","No"]'` — a string,
   not a list. `json.loads()` them a second time. `_loads()` in `data_layer.py`
   handles this.
2. **Gamma's default ordering returns 2020 markets whose price history is
   EMPTY.** You must order by `volume24hr` descending (the default in
   `fetch_markets`) to get recent, liquid markets that actually have a
   `prices-history` series. An unordered sync silently caches markets with zero
   price points and every backtest returns 0 trades.
3. **`prices-history` rejects raw `startTs`/`endTs` past a short window** with
   `"interval is too long"`. Use `interval=` (`1m`/`1h`/`1d`/`max`) +
   `fidelity=` (bar size in minutes) instead. Param is `market=<token_id>`
   (per outcome token), response is `{"history":[{"t":epoch,"p":prob}]}`.

### 1a. Markets and outcomes

```bash
python -m polytester.data_layer --market-list
```

Shows cached markets and how many are resolved. Only **resolved** markets are
backtestable — an open market has no known settlement.

### 1b. Probability history

```bash
python -m polytester.data_layer --fetch-prices MARKET_ID --interval max --fidelity 60
```

Pulls `prices-history` for **each outcome token** of the market (implied
probability time series). Stores `(market_id, outcome, timestamp, price)` in
SQLite. This time series IS the backtest tick stream — see §5.

### 1c. Current order book (live only)

`DataLayer.get_book(token_id)` / `.get_midpoint(token_id)` hit CLOB `/book` and
`/midpoint` for a single outcome token. **No historical book depth is archived
anywhere public** — these are live-only, useful for a paper/live bridge, not
for backtesting. Any "would my limit order have filled" logic must infer from
the price series crossing your level, not from queue position.

### Full sync (once per session)

```bash
python -m polytester.data_layer --sync-all --interval max --fidelity 60 --max-markets 200
```

Fetches highest-volume resolved markets + their per-outcome price history into
`market_db.db`. ~1–2 min for 100–200 markets. Re-run to refresh; `INSERT OR
IGNORE` on prices makes it incremental. **Verified:** 100 markets → ~32k price
points → random baseline PF 0.83–0.97.

---

## 2. Strategy interface

Subclass `BaseStrategy` and implement `on_market_state()`:

```python
from polytester.strategy_interface import BaseStrategy, Order

class RandomBuyer(BaseStrategy):
    """Buy YES or NO 50/50, hold until resolution."""
    
    def __init__(self, market_id, start_cash=1000):
        super().__init__(market_id, start_cash)
    
    def on_market_state(self, market_state, positions):
        """
        Args:
            market_state: {
                'timestamp': datetime,
                'market_id': str,
                'outcomes': ['YES', 'NO'],  # or ['Outcome A', 'Outcome B', ...] for >2
                'prices': {'YES': 0.65, 'NO': 0.35},  # mid-price
                'last_trade': {'price': 0.65, 'size': 10, 'ts': datetime},
                'is_open': bool,  # market still accepting orders
            }
            positions: {
                'YES': {'size': 100, 'entry_price': 0.60, 'current_mark': 0.65},
                'NO': {'size': 0, 'entry_price': None, 'current_mark': None},
            }
        
        Returns:
            List of Order objects (can be empty).
        """
        import random
        
        if not positions['YES']['size'] and market_state['is_open']:
            outcome = random.choice(['YES', 'NO'])
            size = 100  # $100 notional at current price
            price = market_state['prices'][outcome]
            return [Order(outcome=outcome, side='BUY', size=size, price=price)]
        
        return []
```

**Order semantics:**
- `Order(outcome, side, size, price)` — `side` is 'BUY' or 'SELL', `price` is limit price, `size` is position size.
- `side='BUY'` at price 0.65 = betting YES at 65¢, max loss $35 per unit.
- `side='SELL'` at price 0.65 = betting NO at 65¢ (shorting YES), max loss $65 per unit.
- No leverage, no stop/target — positions settle at $1 (winning) or $0 (losing).

Market-open boundaries:
- `on_market_state()` is called on every tick while `market_state['is_open'] == True`.
- At resolution, no more ticks; positions auto-settle.
- If you return orders after resolution, they're ignored.

**Gotcha:** No pre-event order queue — every order is filled at next available trade print (market) or never (limit). Strategies that rely on being filled before X time need different logic; see Limit Order Fills below.

---

## 3. Running a backtest

```python
from polytester.backtest_runner import run_backtest

result = run_backtest(
    strategy_class=RandomBuyer,
    market_ids=['0x123...', '0x456...'],
    start='2026-01-01',
    end='2026-06-30',
    initial_cash=10000,
    timeout_sec=300,
)

print(f"PF: {result['pf']}")
print(f"Max DD: {result['max_dd']}")
print(f"Trades: {len(result['deals'])}")
result['deals'].to_csv('backtest.csv')
```

**Parameters:**
- `market_ids`: List of market IDs to backtest. Auto-fetches data from cache.
- `start`, `end`: ISO date strings or datetime objects.
- `initial_cash`: Starting capital.
- `timeout_sec`: Max wall-clock time per backtest. Raises if exceeded (prevents infinite loops).

**Output:**
```python
{
    'pf': 1.04,  # profit factor
    'max_dd': 0.12,  # max drawdown % from peak
    'sharpe': 0.5,  # Sharpe ratio
    'total_trades': 234,
    'win_rate': 0.52,
    'deals': DataFrame([
        {
            'market_id': '0x123...',
            'outcome': 'YES',
            'entry_ts': datetime(...),
            'entry_price': 0.65,
            'exit_price': 1.0,
            'size': 100,
            'pnl': 35,
            'return_%': 5.4,
        },
        ...
    ]),
}
```

---

## 4. Walk-forward testing (the honest part)

Test on disjoint time windows. **Caveat verified live:** a volume-ordered sync
returns mostly *recent* markets — a 100-market sync on 2026-07-26 only spanned
2026-07-11 → 07-26. Your walk-forward windows must intersect what's actually
cached, or a window returns 0 trades. To backtest older windows, sync with an
explicit `order`/date strategy that reaches back (or accept a rolling recent
window). Check the real span first:

```python
from polytester.data_layer import DataLayer
dl = DataLayer()
r = dl.db.execute("SELECT MIN(timestamp), MAX(timestamp) FROM prices").fetchone()
print(r[0], r[1])  # set your windows inside this
```

Example window set (adjust to your cached span):
- **Train / Val1 / Val2 …** — disjoint slices inside the cached range

```python
from polytester.analyzer import walk_forward_analysis

windows = [
    ('2025-02-01', '2025-04-30', 'train'),
    ('2025-05-01', '2025-06-30', 'val1'),
    ('2025-07-01', '2025-09-30', 'val2'),
    ('2025-10-01', '2025-12-31', 'val3'),
    ('2026-01-01', '2026-03-31', 'val4'),
    ('2026-04-01', '2026-06-30', 'val5'),
]

analysis = walk_forward_analysis(
    strategy_class=RandomBuyer,
    windows=windows,
    market_ids=[...],  # fetch from cache
    initial_cash=10000,
    n_seeds=8,  # Run 8 random seeds per window
)

analysis.to_csv('walk_forward.csv')
# Columns: window, seed, pf, max_dd, sharpe, total_markets, win_rate
```

**Interpretation:**
- **Random baseline:** PF ≈ 0.83–0.97 (verified, all seeds). Anything inside the baseline's seed range is noise.
- **Real edge:** PF plateau above the baseline's *upper tail* across multiple validation windows, not just train.
- A strategy that's "PF 1.20 on train but 0.92 on validate 1" is overfit; reject.

---

## 5. The fill model — price bars ARE the ticks

Polymarket's free historical data is the **probability time series**
(`prices-history`), not fill-level trades. So the harness treats **each price
bar as one "trade print"** at that outcome's probability. `backtest_runner._prices_to_ticks()`
converts cached price rows into the tick stream the simulator consumes.

Consequences (this is the honest-ceiling equivalent of MT5's Model=1 vs
Model=4):
- **Honest on price, blind to depth/queue.** A bar tells you the market's
  probability at that time; it does not tell you the book depth or whether your
  size would have moved the price.
- **Size is set to 1** on synthetic ticks — the simulator fills/marks off last
  price, not size. Don't read the size column as real volume.
- **Market order** fills at the current bar price ± modeled spread (default
  0.5¢/side, `Simulator(bid_ask_cents=...)`).
- **Limit order** fills only when a later bar crosses your price. Thin/short
  markets (a handful of bars) give few fill opportunities — realistic.
- **Settlement** at resolution overrides everything: winning outcome → $1,
  losing → $0. This is verified correct: every deal exits at exactly 0.0 or 1.0.

To upgrade to true fill-level honesty you'd pull the CLOB trades feed or a paid
tick source (PolyTest, pmdata) and feed real prints instead of bars — the
simulator API doesn't change, only the tick source does.

---

## 5b. Trading on price vs holding to resolution

Two ways to make money, both supported:

1. **Hold to resolution** (`RandomBaseline`) — buy an outcome, settle at $0/$1.
   You profit by being *right about the outcome*.
2. **Trade the price mid-market** (`strategies/price_trader.py`) — buy on a
   price signal, **exit at a take-profit / stop-loss price while the market is
   still open**. You profit by being *right about the price move*, regardless of
   the final outcome. Verified: a position bought at 0.35 and sold at 0.45 books
   +$28 even when that outcome ultimately resolves to $0.

**The netting rule (this was a real bug, same class as MT5's `position=ticket`
gotcha):** an order OPPOSITE an open position on the same outcome **closes**
(nets) it at the current price and books the deal — it does NOT open a second,
opposite position. Same-direction orders add to the position. `_close_position`
handles both mid-market exits and resolution settlement; every deal carries an
`exit_reason` of `'exit'` (closed on signal) or `'settle'` (rode to resolution).

**Verified behavior on 100 real resolved markets** (untuned dip-buy signal,
`entry_below=0.40, TP/SL=0.08`): 162 trades, 144 closed mid-market, 18 rode to
settlement. PF 0.22 — *this signal loses money*. That's the point: the harness
does not flatter it. Two lessons the numbers surface:
- The 18 `settle` deals averaged −$628 each vs −$37 for `exit` deals. Positions
  that never hit the stop and rode into a $0 resolution are the tail risk of
  mid-market trading — add a hard **time-stop** (exit N bars before `end_time`)
  before trusting any price strategy.
- Aggregate "final balance" across markets is a display artifact (the runner
  sums per-market PnL against one $10k base). Judge per-market: each market is
  seeded with its own `initial_cash` and stays bounded. Use PF and per-market
  stats, not the summed balance line.

---

## 6. Methodology discipline (mandatory)

Every backtest claimed an edge in this system until tested honestly. **Follow this protocol or results are garbage:**

1. **Walk-forward on disjoint windows.** Train ≠ validate. A config good on train that fails on validate is overfit.
2. **Always run a random baseline** (`strategy_interface.RandomBaseline` — 50/50 random entry, same risk skeleton). **Verified zero-edge line: PF ≈ 0.83–0.97** on real resolved markets (spread drag + the fact that Polymarket prices are roughly efficient). Anything within the baseline's seed range is noise.
3. **Use ≥8 random seeds.** A 4-seed band understates tail variance. **Verified live:** an 8-seed run gave mean 0.97 but a single seed hit **PF 1.19** — that lone seed would read as an "edge" if you ran once. 8 seeds exposed it as noise. This is the whole point of the seed discipline.
4. **Require ≥50–100 markets per window** before trusting a PF. 10 markets is noise.
5. **Ignore tiny-sample cells.** PF 1.20 on 5 markets = garbage. Want ≥50 trades / ≥50 markets before believing anything.
6. **A real edge is a plateau, not a spike.** Good on neighboring parameters AND multiple windows, not one lucky cell.

**Testing checklist before claiming an edge:**
- [ ] Walk-forward analysis: train PF ___, validate1 ___, validate2 ___, validate3 ___
- [ ] All validation PFs ≥ 1.10 (or your chosen threshold)?
- [ ] ≥8 seeds per window; all seeds above random baseline?
- [ ] ≥50 markets per window?
- [ ] Peak PF not on parameters 1 row away from defaults?
- [ ] Same edge holds on a fresh untouched window (validate4 or validate5)?

If any checkbox fails, it's not an edge.

---

## 7. Quick reference

| Task | Command |
|------|---------|
| Sync market data | `python -m polytester.data_layer --sync-all --interval max --fidelity 60 --max-markets 200` |
| List cached markets | `python -m polytester.data_layer --market-list` |
| Fetch prices for 1 market | `python -m polytester.data_layer --fetch-prices MARKET_ID --interval max --fidelity 60` |
| Run demo backtest | `python -m polytester.backtest_runner` (random baseline over all cached resolved markets) |
| Walk-forward analysis | See §4 code snippet; output = CSV ranking by window/seed |
| Random baseline band | `analyzer.random_baseline_band(mids, start, end, n_seeds=8)`; expect PF ≈ 0.83–0.97 |

---

## 8. Gotchas (each one was a debug cycle)

- **Market resolution lag:** Markets don't settle instantly; UMA oracle can lag 4–12 hours. Don't backtest into the resolution window assuming instant settlement.
- **Outcome naming:** 'YES'/'NO' are the defaults, but multi-outcome markets have arbitrary names ('Outcome A', etc.). Check market metadata before hardcoding.
- **Thin/short markets:** Some markets have only a handful of price bars (a 3-hour esports match). An edge found on such markets is **not** an edge.
- **Stale-cache trap:** Gamma's *unordered* default returns 2020 markets with EMPTY price history — sync looks like it succeeds, then every backtest returns 0 trades. Always order by volume/recency (the `fetch_markets` default does).
- **JSON-string fields:** `outcomes`/`outcomePrices`/`clobTokenIds` are JSON-encoded strings, not lists — parse twice.
- **No order queue depth:** You can't know position-in-queue; limit orders either fill at the next price bar crossing your price or don't.
- **Positions don't net:** If you buy YES and later sell YES at a different price, both legs count as separate deals with separate PnL. Simulator doesn't net trades.
- **Cash / margin:** No margin in the backtest. Buy YES at 0.60 for $100 → $40 max loss. Sell NO at 0.40 for $100 → $100 max loss. Budget your position size accordingly.

---

## 9. Example: Random buyer

See `strategies/random_buyer.py` — buys 50/50 YES/NO on every market, holds until resolution. Run to establish baseline:

```bash
python -m polytester.backtest_runner \
  --strategy random_buyer \
  --start 2026-01-01 \
  --end 2026-03-31 \
  --cash 10000 \
  --output results/random_baseline.csv
```

Expected PF: 0.85–0.95. If your strategy's PF is within 10% of this, it's noise, not an edge.

