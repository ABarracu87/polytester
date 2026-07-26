"""Polymarket data fetching and caching.

Endpoints verified live 2026-07-26:
- Gamma /markets: metadata. NOTE outcomes/outcomePrices/clobTokenIds are
  JSON-encoded STRINGS in the response -> json.loads() a second time.
- CLOB /prices-history?market=<token_id>&interval=<1m|1h|1d|max>&fidelity=<min>
  returns {"history":[{"t":epoch,"p":prob}, ...]}. Per OUTCOME TOKEN, not market.
  Raw startTs/endTs are rejected past a short window; use interval= instead.
- CLOB /book, /price?side=, /midpoint : per single token_id (current book only,
  no historical depth is archived anywhere public).
"""
import sqlite3
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import requests
from typing import Optional, Dict, List

DB_PATH = Path(__file__).parent / "market_db.db"
STATE_PATH = Path(__file__).parent / "sync_state.json"

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


def _loads(v):
    """Gamma returns JSON-encoded strings for list fields. Parse if needed."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, ValueError):
            return []
    return v or []


class DataLayer:
    def __init__(self):
        self.db = self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("""
        CREATE TABLE IF NOT EXISTS markets (
            market_id TEXT PRIMARY KEY,
            question TEXT,
            outcomes TEXT,          -- JSON list e.g. ["Yes","No"]
            clob_token_ids TEXT,    -- JSON list, parallel to outcomes
            resolved_outcome TEXT,  -- outcome name, or NULL if unresolved
            closed INTEGER,         -- 1 if market closed/resolved
            end_time INTEGER,       -- unix epoch of resolution
            created_at INTEGER
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            market_id TEXT,
            outcome TEXT,
            timestamp INTEGER,
            price REAL,
            PRIMARY KEY (market_id, outcome, timestamp)
        )
        """)
        conn.commit()
        return conn

    # ------------------------------------------------------------------ markets

    def fetch_markets(self, closed: Optional[bool] = None, max_markets: int = 500,
                      order: str = "volume24hr", ascending: bool = False) -> List[Dict]:
        """Fetch market list from Gamma. closed=True -> resolved markets (needed
        for backtesting, since only resolved markets have a known outcome).

        Default ordering is highest-volume first. NOTE: Gamma's unordered
        default returns oldest (2020) markets, whose prices-history is empty —
        so ordering by volume/recency is required to get usable price data.
        """
        all_markets = []
        offset = 0
        limit = 100

        while len(all_markets) < max_markets:
            params = {"limit": limit, "offset": offset,
                      "order": order, "ascending": str(ascending).lower()}
            if closed is not None:
                params["closed"] = str(closed).lower()
            try:
                resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=15)
                resp.raise_for_status()
                markets = resp.json()
            except requests.exceptions.RequestException as e:
                print(f"Error fetching markets at offset {offset}: {e}")
                break

            if not markets:
                break

            for m in markets:
                self._cache_market(m)
            all_markets.extend(markets)
            offset += limit

        self.db.commit()
        return all_markets

    def _cache_market(self, m: Dict):
        outcomes = _loads(m.get("outcomes"))
        token_ids = _loads(m.get("clobTokenIds"))

        # resolution: closed markets carry the winning outcome in outcomePrices
        # ("1"/"0" once resolved). Map back to the outcome name.
        resolved_outcome = None
        closed = 1 if m.get("closed") else 0
        if closed:
            prices = _loads(m.get("outcomePrices"))
            for name, p in zip(outcomes, prices):
                try:
                    if float(p) >= 0.99:
                        resolved_outcome = name
                        break
                except (ValueError, TypeError):
                    pass

        # end time
        end_ts = None
        iso = m.get("endDateIso") or m.get("endDate")
        if iso:
            try:
                end_ts = int(datetime.fromisoformat(
                    iso.replace("Z", "+00:00")
                ).timestamp())
            except (ValueError, AttributeError):
                pass
        if end_ts is None:
            end_ts = int(time.time()) + 86400 * 365

        self.db.execute("""
        INSERT OR REPLACE INTO markets
        (market_id, question, outcomes, clob_token_ids, resolved_outcome,
         closed, end_time, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(m["id"]),
            m.get("question", ""),
            json.dumps(outcomes),
            json.dumps(token_ids),
            resolved_outcome,
            closed,
            end_ts,
            int(time.time()),
        ))

    # ------------------------------------------------------------------- prices

    def fetch_prices(self, market_id: str, interval: str = "max",
                     fidelity: int = 60) -> List[Dict]:
        """Fetch probability history for EACH outcome of a market.

        Uses CLOB /prices-history per outcome token. interval one of
        1m/1h/1d/max (Polymarket's own presets); fidelity = bar size in minutes.
        Stores (market_id, outcome, timestamp, price) rows.
        """
        m = self.get_market(market_id)
        if not m:
            print(f"Market {market_id} not in cache; run fetch_markets first")
            return []

        outcomes = _loads(m["outcomes"])
        token_ids = _loads(m["clob_token_ids"])
        if not token_ids:
            print(f"Market {market_id} has no clob token ids")
            return []

        all_points = []
        for outcome, tid in zip(outcomes, token_ids):
            try:
                resp = requests.get(
                    f"{CLOB_API}/prices-history",
                    params={"market": tid, "interval": interval, "fidelity": fidelity},
                    timeout=20,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.RequestException as e:
                print(f"  prices-history failed for {outcome}: {e}")
                continue

            history = data.get("history", []) if isinstance(data, dict) else data
            for pt in history:
                ts = int(pt["t"])
                price = float(pt["p"])
                self.db.execute("""
                INSERT OR IGNORE INTO prices (market_id, outcome, timestamp, price)
                VALUES (?, ?, ?, ?)
                """, (market_id, outcome, ts, price))
                all_points.append({"outcome": outcome, "timestamp": ts, "price": price})

        self.db.commit()
        return all_points

    # --------------------------------------------------------------- live quote

    def get_book(self, token_id: str) -> Dict:
        """Current order book for one outcome token (live only)."""
        resp = requests.get(f"{CLOB_API}/book", params={"token_id": token_id}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_midpoint(self, token_id: str) -> Optional[float]:
        resp = requests.get(f"{CLOB_API}/midpoint", params={"token_id": token_id}, timeout=15)
        if resp.ok:
            return float(resp.json().get("mid"))
        return None

    # ----------------------------------------------------------------- getters

    def get_market(self, market_id: str) -> Optional[Dict]:
        row = self.db.execute(
            "SELECT * FROM markets WHERE market_id = ?", (market_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_prices(self, market_id: str, start: datetime, end: datetime) -> List[Dict]:
        rows = self.db.execute("""
        SELECT * FROM prices
        WHERE market_id = ? AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp
        """, (market_id, int(start.timestamp()), int(end.timestamp()))).fetchall()
        return [dict(r) for r in rows]

    def list_markets(self, resolved_only: bool = False) -> List[Dict]:
        q = "SELECT * FROM markets"
        if resolved_only:
            q += " WHERE resolved_outcome IS NOT NULL"
        return [dict(r) for r in self.db.execute(q).fetchall()]


def sync_all(interval: str = "max", fidelity: int = 60, max_markets: int = 200):
    """Fetch resolved markets + their price history into the cache."""
    dl = DataLayer()
    print("Fetching resolved markets from Gamma...")
    markets = dl.fetch_markets(closed=True, max_markets=max_markets)
    print(f"Cached {len(markets)} markets")

    resolved = dl.list_markets(resolved_only=True)
    print(f"{len(resolved)} have a known winning outcome; pulling price history...")
    for i, m in enumerate(resolved):
        if i % 20 == 0:
            print(f"  {i}/{len(resolved)}")
        dl.fetch_prices(m["market_id"], interval=interval, fidelity=fidelity)
        time.sleep(0.05)  # ponytail: fixed throttle, swap for backoff if 429s appear
    print("Sync complete")
    return dl


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync-all", action="store_true")
    parser.add_argument("--market-list", action="store_true")
    parser.add_argument("--fetch-prices", type=str, help="market ID")
    parser.add_argument("--interval", type=str, default="max")
    parser.add_argument("--fidelity", type=int, default=60)
    parser.add_argument("--max-markets", type=int, default=200)
    args = parser.parse_args()

    dl = DataLayer()

    if args.sync_all:
        sync_all(interval=args.interval, fidelity=args.fidelity, max_markets=args.max_markets)
    elif args.market_list:
        markets = dl.list_markets()
        resolved = [m for m in markets if m["resolved_outcome"]]
        print(f"Markets cached: {len(markets)} ({len(resolved)} resolved)")
        for m in markets[:10]:
            print(f"  [{m['resolved_outcome'] or 'open'}] {m['question'][:60]}")
    elif args.fetch_prices:
        pts = dl.fetch_prices(args.fetch_prices, interval=args.interval, fidelity=args.fidelity)
        print(f"Fetched {len(pts)} price points")
