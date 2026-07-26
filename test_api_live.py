"""
Test Polymarket API endpoints live.
Run this to verify which endpoints work and what format they return.
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


def test_gamma_markets():
    """Test Gamma API markets endpoint."""
    print("\n" + "=" * 80)
    print("TEST 1: Gamma API - Markets List")
    print("=" * 80)

    try:
        resp = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 2, "active": True, "order": "volume24hr", "ascending": False},
            timeout=10
        )
        resp.raise_for_status()

        markets = resp.json()
        print(f"✓ Status: {resp.status_code}")
        print(f"✓ Markets returned: {len(markets)}")

        if markets:
            import json as _json
            m = markets[0]
            # Gamma returns outcomes/outcomePrices/clobTokenIds as JSON strings
            outcomes = _json.loads(m.get('outcomes', '[]'))
            prices = _json.loads(m.get('outcomePrices', '[]'))
            print(f"\nFirst market sample:")
            print(f"  ID: {str(m.get('id'))[:20]}...")
            print(f"  Question: {str(m.get('question'))[:80]}")
            print(f"  Outcomes: {outcomes}")
            print(f"  Outcome prices: {prices}")
            print(f"  Active: {m.get('active')}")

            # Check required fields
            required = ['id', 'question', 'outcomes', 'outcomePrices', 'clobTokenIds']
            missing = [k for k in required if k not in m]
            if missing:
                print(f"  ⚠ Missing fields: {missing}")
            else:
                print(f"  ✓ All required fields present")

        return True

    except httpx.HTTPError as e:
        print(f"✗ HTTP Error: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_clob_book():
    """Test CLOB API orderbook endpoint."""
    print("\n" + "=" * 80)
    print("TEST 2: CLOB API - Orderbook")
    print("=" * 80)

    try:
        # First get a market to extract token ID
        print("Step 1: Fetching a market to get token ID...")
        resp_markets = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 1, "active": True},
            timeout=10
        )
        resp_markets.raise_for_status()

        markets = resp_markets.json()
        if not markets:
            print("✗ No active markets found")
            return False

        import json as _json
        market = markets[0]
        # clobTokenIds is a JSON-ENCODED STRING, e.g. '["123...","456..."]'.
        # Parse it, then use ONE token id — the CLOB endpoints 404 on a list.
        token_ids = _json.loads(market.get('clobTokenIds', '[]'))
        if not token_ids:
            print("✗ No CLOB token IDs in market")
            print(f"   Available keys: {list(market.keys())}")
            return False

        token_id = token_ids[0]
        print(f"✓ Found market: {market['question'][:60]}")
        print(f"✓ Token ID: {token_id[:20]}...")

        # Now fetch the book
        print(f"\nStep 2: Fetching orderbook for token {token_id[:20]}...")
        resp_book = httpx.get(
            "https://clob.polymarket.com/book",
            params={"token_id": token_id},
            timeout=10
        )
        resp_book.raise_for_status()

        book = resp_book.json()
        print(f"✓ Status: {resp_book.status_code}")
        print(f"✓ Response keys: {list(book.keys())}")

        if 'bids' in book and book['bids']:
            print(f"\n  Best bid: {book['bids'][0]}")
        if 'asks' in book and book['asks']:
            print(f"  Best ask: {book['asks'][0]}")
        if 'mid' in book:
            print(f"  Midpoint: {book['mid']}")

        return True

    except httpx.HTTPError as e:
        print(f"✗ HTTP Error: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False


def test_clob_price():
    """Test CLOB API price endpoint."""
    print("\n" + "=" * 80)
    print("TEST 3: CLOB API - Price")
    print("=" * 80)

    try:
        # Get a token ID
        print("Step 1: Fetching a market...")
        resp_markets = httpx.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 1, "active": True},
            timeout=10
        )
        resp_markets.raise_for_status()

        markets = resp_markets.json()
        if not markets:
            print("✗ No markets returned")
            return False

        import json as _json
        market = markets[0]
        token_ids = _json.loads(market.get('clobTokenIds', '[]'))
        if not token_ids:
            print(f"✗ No CLOB token IDs. Available: {list(market.keys())}")
            return False

        token_id = token_ids[0]
        print(f"✓ Token ID: {token_id[:20]}...")

        # Fetch price
        print(f"\nStep 2: Fetching price for both sides...")
        for side in ["BUY", "SELL"]:
            resp = httpx.get(
                "https://clob.polymarket.com/price",
                params={"token_id": token_id, "side": side},
                timeout=10
            )
            resp.raise_for_status()
            price = resp.json()
            print(f"  {side}: {price}")

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
        "CLOB Book": test_clob_book(),
        "CLOB Price": test_clob_price(),
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
        print("✓ You can now run: python -m polytester.data_layer --sync-all")
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
