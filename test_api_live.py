"""
Test Polymarket API endpoints live.
Run this to verify which endpoints work and what format they return.

Endpoints under test (verified reality):
  - Gamma  https://gamma-api.polymarket.com/markets      (market metadata)
  - CLOB   https://clob.polymarket.com/prices-history     (per-outcome probability series)
  - CLOB   https://clob.polymarket.com/book                (live orderbook, single token)
  - CLOB   https://clob.polymarket.com/price               (live price, single token)
  - CLOB   https://clob.polymarket.com/midpoint            (live midpoint, single token)
"""
import sys
try:
    import httpx
except ImportError:
    print("Installing httpx...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx", "-q"])
    import httpx

import json
from datetime import datetime

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com"


def _loads(v):
    """Gamma list fields (outcomes/outcomePrices/clobTokenIds) are JSON-encoded strings."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return v
    return v


def _first_token_id():
    """Fetch one market and return (market, single parsed CLOB token id str)."""
    resp = httpx.get(GAMMA, params={"limit": 1, "active": True,
                                    "order": "volume24hr", "ascending": False}, timeout=10)
    resp.raise_for_status()
    markets = resp.json()
    if not markets:
        return None, None
    market = markets[0]
    token_ids = _loads(market.get("clobTokenIds", "[]"))
    if not token_ids or not isinstance(token_ids, list):
        return market, None
    return market, token_ids[0]


def test_gamma_markets():
    """Test Gamma API markets endpoint."""
    print("\n" + "=" * 80)
    print("TEST 1: Gamma API - Markets List")
    print("=" * 80)

    try:
        resp = httpx.get(
            GAMMA,
            params={"limit": 2, "active": True, "order": "volume24hr", "ascending": False},
            timeout=10,
        )
        resp.raise_for_status()

        markets = resp.json()
        print(f"✓ Status: {resp.status_code}")
        print(f"✓ Markets returned: {len(markets)}")

        if markets:
            m = markets[0]
            # Gamma returns outcomes/outcomePrices/clobTokenIds as JSON-encoded strings
            outcomes = _loads(m.get("outcomes", "[]"))
            prices = _loads(m.get("outcomePrices", "[]"))
            token_ids = _loads(m.get("clobTokenIds", "[]"))
            print(f"\nFirst market sample:")
            print(f"  ID: {str(m.get('id'))[:20]}...")
            print(f"  Question: {str(m.get('question'))[:80]}")
            print(f"  Outcomes: {outcomes}")
            print(f"  Outcome prices: {prices}")
            print(f"  CLOB token IDs: {[str(t)[:12] + '...' for t in token_ids]}")
            print(f"  Closed at: {m.get('closedAt')}")
            print(f"  Active: {m.get('active')}")

            # Check required fields (all present, as JSON-encoded strings)
            required = ['id', 'question', 'outcomes', 'clobTokenIds', 'outcomePrices']
            missing = [k for k in required if k not in m]
            if missing:
                print(f"  ⚠ Missing fields: {missing}")
                return False
            print(f"  ✓ All required fields present")

        return True

    except httpx.HTTPError as e:
        print(f"✗ HTTP Error: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_clob_prices_history():
    """Test CLOB API prices-history endpoint (the historical data source)."""
    print("\n" + "=" * 80)
    print("TEST 2: CLOB API - Prices-History")
    print("=" * 80)

    try:
        print("Step 1: Fetching a market to get a token ID...")
        market, token_id = _first_token_id()
        if market is None:
            print("✗ No active markets found")
            return False
        if not token_id:
            print(f"✗ No CLOB token IDs. Available keys: {list(market.keys())}")
            return False
        print(f"✓ Market: {str(market['question'])[:60]}")
        print(f"✓ Token ID: {str(token_id)[:20]}...")

        print(f"\nStep 2: Fetching prices-history (interval=max, fidelity=60)...")
        resp = httpx.get(
            f"{CLOB}/prices-history",
            params={"market": token_id, "interval": "max", "fidelity": 60},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"✓ Status: {resp.status_code}")
        print(f"✓ Response keys: {list(data.keys())}")

        history = data.get("history", [])
        print(f"✓ History points: {len(history)}")
        if history:
            print(f"  First point: {history[0]}")   # {'t': epoch, 'p': prob}
            print(f"  Last point:  {history[-1]}")
        else:
            print("  ⚠ Empty history (market may be too new or illiquid)")

        return "history" in data

    except httpx.HTTPError as e:
        print(f"✗ HTTP Error: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_clob_book():
    """Test CLOB API orderbook endpoint (live, single token)."""
    print("\n" + "=" * 80)
    print("TEST 3: CLOB API - Orderbook")
    print("=" * 80)

    try:
        market, token_id = _first_token_id()
        if market is None:
            print("✗ No active markets found")
            return False
        if not token_id:
            print(f"✗ No CLOB token IDs. Available keys: {list(market.keys())}")
            return False
        print(f"✓ Token ID: {str(token_id)[:20]}...")

        print(f"\nFetching orderbook...")
        resp = httpx.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        book = resp.json()
        print(f"✓ Status: {resp.status_code}")
        print(f"✓ Response keys: {list(book.keys())}")

        if book.get("bids"):
            print(f"  Best bid: {book['bids'][0]}")
        if book.get("asks"):
            print(f"  Best ask: {book['asks'][0]}")

        return True

    except httpx.HTTPError as e:
        print(f"✗ HTTP Error: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_clob_price_and_midpoint():
    """Test CLOB API price and midpoint endpoints (live, single token)."""
    print("\n" + "=" * 80)
    print("TEST 4: CLOB API - Price and Midpoint")
    print("=" * 80)

    try:
        market, token_id = _first_token_id()
        if market is None:
            print("✗ No active markets found")
            return False
        if not token_id:
            print(f"✗ No CLOB token IDs. Available keys: {list(market.keys())}")
            return False
        print(f"✓ Token ID: {str(token_id)[:20]}...")

        print(f"\nStep 1: Fetching price for both sides...")
        for side in ["BUY", "SELL"]:
            resp = httpx.get(f"{CLOB}/price",
                             params={"token_id": token_id, "side": side}, timeout=10)
            resp.raise_for_status()
            print(f"  {side}: {resp.json()}")

        print(f"\nStep 2: Fetching midpoint...")
        resp = httpx.get(f"{CLOB}/midpoint", params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        print(f"  midpoint: {resp.json()}")

        return True

    except httpx.HTTPError as e:
        print(f"✗ HTTP Error: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def main():
    print("\n" + "#" * 80)
    print("# POLYMARKET API LIVE TEST")
    print(f"# {datetime.utcnow().isoformat()} UTC")
    print("#" * 80)

    results = {
        "Gamma Markets": test_gamma_markets(),
        "CLOB Prices-History": test_clob_prices_history(),
        "CLOB Book": test_clob_book(),
        "CLOB Price/Midpoint": test_clob_price_and_midpoint(),
    }

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")

    all_pass = all(results.values())
    if all_pass:
        print("\n✓ All tests passed. APIs are live and responding.")
        print("✓ You can now run: python -m polytester.data_layer "
              "--sync-all --interval max --fidelity 60 --max-markets 200")
    else:
        print("\n✗ Some tests failed. Check output above.")
        print("✗ Possible reasons:")
        print("  - Endpoint URL changed")
        print("  - Response format changed")
        print("  - Rate limit hit (wait a minute)")
        print("  - Network issue")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
