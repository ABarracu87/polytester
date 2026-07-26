# Polymarket API Integration — Options & Solutions

**Research complete.** Three paths forward, ranked by effort & reliability.

---

## Option 1: REST Gamma API (Easiest, Recommended for Backtesting)

**Effort:** Low | **Cost:** Free | **Auth:** None

Official endpoints (verified live 2026-07-26):
```
https://gamma-api.polymarket.com/markets
https://clob.polymarket.com/prices-history?market=<token_id>&interval=max&fidelity=60
https://clob.polymarket.com/book?token_id=...
https://clob.polymarket.com/price?token_id=...
https://clob.polymarket.com/midpoint?token_id=...
```

`prices-history` is the historical data source (per-outcome probability time
series); `/book`, `/price`, `/midpoint` are live-only (single outcome token).

### Quick Test

```bash
# Get top 10 markets
curl -s "https://gamma-api.polymarket.com/markets?limit=10&active=true&order=volume24hr&ascending=false" | jq '.[0]'

# Get orderbook for a token
curl -s "https://clob.polymarket.com/book?token_id=0x..." | jq '{bids,asks,mid}'
```

### Python Integration

Use `httpx` or `requests`:

```python
import httpx

client = httpx.Client(timeout=10.0)

# Fetch active markets
markets = client.get(
    "https://gamma-api.polymarket.com/markets",
    params={"limit": 100, "active": True, "order": "volume24hr", "ascending": False}
).json()

import json

print(f"Found {len(markets)} markets")
for m in markets[:5]:
    # outcomes / outcomePrices / clobTokenIds come back as JSON-encoded STRINGS
    outcomes = json.loads(m.get("outcomes", "[]"))
    prices = json.loads(m.get("outcomePrices", "[]"))
    print(f"  {m['question'][:60]}")
    print(f"    Outcomes: {outcomes}")
    print(f"    YES price: {prices[0] if prices else None}")
```

**Response format (matches polytester expectations):**
```json
{
  "id": "0x123...",
  "question": "Will candidate X win?",
  "slug": "will-candidate-x-win",
  "conditionId": "0x...",
  "createdAt": "2026-01-01T00:00:00Z",
  "closedAt": "2026-06-30T23:59:00Z",
  "resolvedAt": null,
  "resolvedOutcome": null,
  "outcomes": "[\"Yes\", \"No\"]",
  "clobTokenIds": "[\"0xabc\", \"0xdef\"]",
  "outcomePrices": "[\"0.65\", \"0.35\"]",
  "volume24hr": 50000.0,
  "active": true
}
```

**Note:** `outcomes`, `clobTokenIds`, and `outcomePrices` are returned as
JSON-encoded strings, not arrays — `json.loads()` each before use. Outcome
labels and their CLOB token IDs are positionally aligned across `outcomes` and
`clobTokenIds`.

**Update polytester's `data_layer.py`:**

```python
def fetch_markets_rest(self, limit=100, active=True):
    """Fetch from Gamma API (REST, no auth required)."""
    resp = requests.get(
        "https://gamma-api.polymarket.com/markets",
        params={"limit": limit, "active": active, "order": "volume24hr", "ascending": False},
        timeout=10
    )
    resp.raise_for_status()
    
    markets = resp.json()
    for m in markets:
        # outcomes is a JSON-encoded string; re-encode after parsing to store canonically
        outcomes = json.dumps(json.loads(m.get("outcomes", "[]")))
        end_ts = int(datetime.fromisoformat(m["closedAt"].replace("Z", "+00:00")).timestamp())
        
        self.db.execute("""
        INSERT OR REPLACE INTO markets
        (market_id, question, outcomes, resolved_outcome, end_time, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (m["id"], m["question"], outcomes, m.get("resolvedOutcome"), end_ts, int(time.time())))
    
    self.db.commit()
    return markets
```

**Gotcha:** Gamma API has **per-endpoint rate limits** (300 req/10s for `/markets`). Implement caching aggressively or add `time.sleep(0.05)` between requests.

---

## Option 2: Official py-sdk (Recommended for Production)

**Effort:** Medium | **Cost:** Free | **Auth:** Optional (needed for trading, not for reads)

Official SDK: https://github.com/Polymarket/py-sdk

### Install

```bash
pip install py-clob-client  # Legacy (still works)
# OR
# Check GitHub for new unified py-sdk package name
```

### Usage

```python
from py_clob_client.client import ClobClient

client = ClobClient(
    host="https://clob.polymarket.com",
    signature_type="EOA"  # For reading only, no auth needed
)

# Get markets
markets = client.get_markets()

# Get prices
prices = client.get_price(token_id="0x...", side="BUY")

# Get orderbook
book = client.get_order_book(token_id="0x...")
```

**Advantages:**
- Official, maintained by Polymarket
- Handles auth automatically for trading
- Type hints, error handling
- Tested against live API

**Disadvantage:**
- Extra dependency; slightly heavier than REST

**Update polytester:**

```python
from py_clob_client.client import ClobClient

class DataLayerSDK(DataLayer):
    def __init__(self):
        super().__init__()
        self.clob = ClobClient(host="https://clob.polymarket.com")
    
    def fetch_markets(self):
        """Use official SDK."""
        markets = self.clob.get_markets()
        # ... cache as before
        return markets
```

---

## Option 3: GraphQL Subgraph (Best for Complex Historical Queries)

**Effort:** Medium | **Cost:** Free tier (100k queries/month) | **Auth:** None

Use The Graph's Polymarket subgraph for historical data queries:
- Endpoint: `https://api.thegraph.com/subgraphs/name/[polymarket-subgraph-id]`
- Latency: ~2 seconds (updates on Polygon blocks)
- Data: Markets, trades, positions, liquidity, PnL

### Example Query

```graphql
query GetMarketHistory {
  fixedProductMarketMakers(
    first: 100
    orderBy: creationTimestamp
    orderDirection: desc
    where: {
      active: true
      scaledCollateralVolume_gt: 10000
    }
  ) {
    id
    conditions
    scaledCollateralVolume
    tradesQuantity
    outcomeTokenPrices
    creationTimestamp
  }
  
  # Get all trades for a market in a time window
  trades(
    first: 1000
    orderBy: timestamp
    where: {
      market: "0x..."
      timestamp_gte: 1704067200
      timestamp_lte: 1704153600
    }
  ) {
    id
    price
    amount
    timestamp
    isMaker
    userTrader
  }
}
```

### Python Integration

```python
import requests

SUBGRAPH = "https://api.thegraph.com/subgraphs/name/[polymarket-subgraph]"

def get_market_history(market_id, start_ts, end_ts):
    query = """
    query {
      trades(
        first: 1000
        where: {
          market: "%s"
          timestamp_gte: %d
          timestamp_lte: %d
        }
      ) {
        id
        price
        amount
        timestamp
        isMaker
      }
    }
    """ % (market_id, start_ts, end_ts)
    
    resp = requests.post(SUBGRAPH, json={"query": query}).json()
    return resp["data"]["trades"]
```

**Advantage:** Complex joins, aggregations, and deep history in one query.

**Gotcha:** Subgraph may lag 2–10 seconds behind latest block; not suitable for real-time trading.

---

## Option 4: poly_data Tool (Pre-Processing Helper)

**Effort:** Low | **Cost:** Free | **Scope:** Offline data retrieval

CLI tool that fetches all Polymarket data and exports CSV:
https://github.com/warproxxx/poly_data

### Install & Use

```bash
pip install poly_data

# Fetch all resolved markets with trade history
poly_data --markets-only
poly_data --trades --from-date 2025-01-01 --to-date 2026-06-30 --output markets.csv

# Outputs: markets.csv, trades.csv (ready for pandas)
```

Then load into polytester:

```python
import pandas as pd

markets_df = pd.read_csv("markets.csv")
trades_df = pd.read_csv("trades.csv")

# Map to polytester format and cache
for _, market in markets_df.iterrows():
    # ... insert into db
```

**Advantage:** Pre-processed, high-quality data; no real-time fetching.

**Gotcha:** Only historical data (closed markets); not live.

---

## Option 5: Paid Services (If Free Tier Insufficient)

### PolyTest.io
- **Cost:** Free tier + paid for advanced
- **Provides:** Historical 8-level orderbook snapshots, bid/ask spreads, liquidity
- **API:** REST
- **Latency:** 5–30 minute granularity
- **Use case:** Accurate fill modeling for limit orders

### pmdata.dev
- **Cost:** Paid API key
- **Provides:** Pre-cleaned, deduplicated, indexed Polymarket data
- **Use case:** Fast access to exact trade ticks without API rate limiting

---

## Recommendation

**Use Option 1 (REST Gamma) for Polytester:**

1. **Why:** Simplest, no auth, matches polytester's current code shape
2. **How:** Update `data_layer.py` to use exact current endpoints
3. **Test first:** 
   ```bash
   curl "https://gamma-api.polymarket.com/markets?limit=1&active=true"
   curl "https://clob.polymarket.com/book?token_id=0x..."
   ```
4. **Add caching:** Fetch once, cache to SQLite, reuse

**If rate limits are a problem:** Add `time.sleep(0.1)` between requests or use Option 4 (poly_data) for offline data.

**If you need tick-level orderbook accuracy:** Upgrade to **PolyTest** or **py-sdk** later.

---

## Next Action

1. Test live endpoint (copy curl commands below)
2. Verify response matches polytester's `data_layer.py` expectations
3. Patch `fetch_markets()` if needed
4. Run: `python -m polytester.data_layer --sync-all --interval max --fidelity 60 --max-markets 200`

---

## Curl Commands (Paste & Run)

### Test Gamma API
```bash
curl -s "https://gamma-api.polymarket.com/markets?limit=1&active=true&order=volume24hr&ascending=false" | jq '.[0] | {id, question, outcomes, clobTokenIds, outcomePrices, closedAt}'
```

### Test CLOB Book
```bash
# First, get a token ID from Gamma response above
TOKEN_ID="0x..."
curl -s "https://clob.polymarket.com/book?token_id=${TOKEN_ID}" | jq '{bids, asks, mid}'
```

### Test CLOB Price
```bash
curl -s "https://clob.polymarket.com/price?token_id=${TOKEN_ID}&side=BUY" | jq .
```

### Test CLOB Prices-History (the historical data source)
```bash
curl -s "https://clob.polymarket.com/prices-history?market=${TOKEN_ID}&interval=max&fidelity=60" | jq '.history[0:3]'
```

---

## Links

- **Official Docs:** https://docs.polymarket.com
- **py-sdk GitHub:** https://github.com/Polymarket/py-sdk
- **Subgraph Guide:** https://thegraph.com/docs/en/subgraphs/guides/polymarket/
- **poly_data:** https://github.com/warproxxx/poly_data
- **PolyTest:** https://polytest.io
- **API Rate Limits Guide:** https://agentbets.ai/guides/polymarket-rate-limits-guide/

