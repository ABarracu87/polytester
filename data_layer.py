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
        # Added 2026-08-05. Gamma exposes the market's own quoted spread and a
        # sports-category flag; both are worth keeping. The real spread median is
        # 0.0010 (0.1c), 5x TIGHTER than the simulator's 0.5c default, so backtests
        # using the default are pessimistic on cost. Migrate in place so existing
        # caches keep working.
        have = {r[1] for r in conn.execute("PRAGMA table_info(markets)")}
        for col, decl in (("spread", "REAL"), ("sports_type", "TEXT"), ("volume", "REAL")):
            if col not in have:
                conn.execute(f"ALTER TABLE markets ADD COLUMN {col} {decl}")
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
                      order: str = "volume", ascending: bool = False,
                      tag_id: Optional[int] = None,
                      exclude_sports: bool = False) -> List[Dict]:
        """Fetch market list from Gamma. closed=True -> resolved markets (needed
        for backtesting, since only resolved markets have a known outcome).

        `order` defaults to **total** volume. Do NOT use "volume24hr": on closed
        markets it returns only the most recently resolved high-churn events, which
        caps the reachable history at a few WEEKS (measured 2026-08-05: volume24hr
        spans 2026-07-26..2026-08-12, while volume spans 2022-03-30..2026-08-09).
        Gamma's unordered default is also useless — it returns oldest (2020) markets
        whose prices-history is empty.

        `tag_id` filters to a Polymarket category. Empirically mapped 2026-08-05 by
        probing ids against /markets:
            1  sports          2  politics/news     8,10-14 sports
            3-5 gaming         6-7 Elon/social      15 elections
            16-18 awards       19-21 crypto         154 Middle East
        Use tag_id=2 or 15 for news-driven markets; the unfiltered top-volume
        universe is ~82% sports.

        `exclude_sports` drops anything carrying Gamma's own `sportsMarketType`
        flag — more reliable than regex on the question text.

        NOTE Gamma rejects offset >= ~5000 with HTTP 422, so the reachable universe
        is a few thousand markets per query; narrow with tag_id rather than paging.
        """
        all_markets = []
        offset = 0
        limit = 100

        while len(all_markets) < max_markets:
            params = {"limit": limit, "offset": offset,
                      "order": order, "ascending": str(ascending).lower()}
            if closed is not None:
                params["closed"] = str(closed).lower()
            if tag_id is not None:
                params["tag_id"] = tag_id
            try:
                resp = requests.get(f"{GAMMA_API}/markets", params=params, timeout=20)
                resp.raise_for_status()
                markets = resp.json()
            except requests.exceptions.RequestException as e:
                # 422 past the offset ceiling is expected, not an error worth shouting about
                if getattr(e.response, "status_code", None) == 422:
                    print(f"  offset ceiling reached at {offset}")
                else:
                    print(f"Error fetching markets at offset {offset}: {e}")
                break

            # Gamma returns a bare string on some error paths; guard before iterating
            if not isinstance(markets, list) or not markets:
                break
            markets = [m for m in markets if isinstance(m, dict)]
            if exclude_sports:
                markets = [m for m in markets if not m.get("sportsMarketType")]

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

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        self.db.execute("""
        INSERT OR REPLACE INTO markets
        (market_id, question, outcomes, clob_token_ids, resolved_outcome,
         closed, end_time, created_at, spread, sports_type, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(m["id"]),
            m.get("question", ""),
            json.dumps(outcomes),
            json.dumps(token_ids),
            resolved_outcome,
            closed,
            end_ts,
            int(time.time()),
            _num(m.get("spread")),
            m.get("sportsMarketType") or None,
            _num(m.get("volumeNum") if m.get("volumeNum") is not None else m.get("volume")),
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


def sync_all(interval: str = "max", fidelity: int = 60, max_markets: int = 200,
             order: str = "volume", tag_id: Optional[int] = None,
             exclude_sports: bool = False, only_new: bool = True):
    """Fetch resolved markets + their price history into the cache.

    See fetch_markets for the tag_id map. For news-driven markets use
    `tag_id=2` (politics) or `tag_id=15` (elections) — the unfiltered
    top-volume universe is ~82% sports.
    """
    dl = DataLayer()
    print(f"Fetching resolved markets from Gamma (order={order}, tag_id={tag_id}, "
          f"exclude_sports={exclude_sports})...")
    markets = dl.fetch_markets(closed=True, max_markets=max_markets, order=order,
                               tag_id=tag_id, exclude_sports=exclude_sports)
    print(f"Cached {len(markets)} markets")

    resolved = dl.list_markets(resolved_only=True)
    if only_new:
        # Skip markets already ATTEMPTED, not merely those that already have rows.
        #
        # Polymarket's free /prices-history is a ~2-MONTH ROLLING WINDOW: markets
        # resolved before it return HTTP 200 with an empty history, permanently.
        # Measured 2026-08-05: of 4,575 resolved markets only 564 had any history, all
        # resolving 2026-06..2026-08, while the 4,011 without spanned 2022-2027.
        # Keying "todo" on the prices table alone therefore re-requests those 4,011
        # known-empty markets on EVERY run, forever. Record the attempt instead.
        dl.db.execute("CREATE TABLE IF NOT EXISTS price_fetch_log ("
                      "market_id TEXT PRIMARY KEY, fetched_at INTEGER, n_points INTEGER)")
        dl.db.commit()
        tried = {r[0] for r in dl.db.execute("SELECT market_id FROM price_fetch_log")}
        have = {r[0] for r in dl.db.execute("SELECT DISTINCT market_id FROM prices")}
        skip = tried | have
        todo = [m for m in resolved if m["market_id"] not in skip]
        print(f"{len(resolved)} resolved in cache; {len(have)} have history, "
              f"{len(tried - have)} known-empty (skipped); {len(todo)} to fetch")
    else:
        todo = resolved
        print(f"{len(resolved)} have a known winning outcome; pulling price history...")

    for i, m in enumerate(todo):
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        pts = dl.fetch_prices(m["market_id"], interval=interval, fidelity=fidelity)
        if only_new:
            dl.db.execute(
                "INSERT OR REPLACE INTO price_fetch_log VALUES (?, ?, ?)",
                (m["market_id"], int(time.time()), len(pts)))
            dl.db.commit()
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
    parser.add_argument("--order", type=str, default="volume",
                        help="volume (default, ~4yr reach) | volume24hr (weeks only) | liquidity")
    parser.add_argument("--tag-id", type=int, default=None,
                        help="Polymarket category: 2=politics/news, 15=elections, 1=sports")
    parser.add_argument("--exclude-sports", action="store_true",
                        help="drop markets carrying Gamma's sportsMarketType flag")
    parser.add_argument("--refetch-prices", action="store_true",
                        help="re-pull price history even for markets that already have it")
    args = parser.parse_args()

    dl = DataLayer()

    if args.sync_all:
        sync_all(interval=args.interval, fidelity=args.fidelity,
                 max_markets=args.max_markets, order=args.order, tag_id=args.tag_id,
                 exclude_sports=args.exclude_sports, only_new=not args.refetch_prices)
    elif args.market_list:
        markets = dl.list_markets()
        resolved = [m for m in markets if m["resolved_outcome"]]
        print(f"Markets cached: {len(markets)} ({len(resolved)} resolved)")
        for m in markets[:10]:
            print(f"  [{m['resolved_outcome'] or 'open'}] {m['question'][:60]}")
    elif args.fetch_prices:
        pts = dl.fetch_prices(args.fetch_prices, interval=args.interval, fidelity=args.fidelity)
        print(f"Fetched {len(pts)} price points")
