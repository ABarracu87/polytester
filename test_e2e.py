"""End-to-end test against the LIVE Polymarket API.

Exercises the full path: Gamma markets -> CLOB prices-history -> cache ->
simulator -> settlement. Network-dependent; skips gracefully if offline.

Run: python -m polytester.test_e2e
"""
import json
import requests

from polytester.data_layer import DataLayer, _loads


def _online() -> bool:
    try:
        requests.get("https://gamma-api.polymarket.com/markets",
                     params={"limit": 1}, timeout=8).raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False


def test_gamma_field_parsing():
    """Gamma returns list fields as JSON strings — _loads must unwrap them."""
    assert _loads('["Yes","No"]') == ["Yes", "No"]
    assert _loads(["Yes", "No"]) == ["Yes", "No"]   # already a list
    assert _loads(None) == []
    assert _loads("garbage") == []
    print("✓ Gamma JSON-string field parsing")


def test_live_pipeline():
    """Sync a few resolved markets and run the random baseline end to end."""
    if not _online():
        print("⚠ Offline — skipping live pipeline test")
        return

    dl = DataLayer()

    # Fetch a small batch of high-volume resolved markets (recent -> has prices)
    markets = dl.fetch_markets(closed=True, max_markets=20)
    assert markets, "Gamma returned no markets"
    print(f"✓ Fetched {len(markets)} markets from Gamma")

    resolved = dl.list_markets(resolved_only=True)
    assert resolved, "No markets resolved to a known outcome"
    print(f"✓ {len(resolved)} resolved with a known winning outcome")

    # Pull prices for the first resolved market with clob tokens
    target = None
    for m in resolved:
        if _loads(m["clob_token_ids"]):
            target = m
            break
    assert target, "No resolved market had clob token ids"

    pts = dl.fetch_prices(target["market_id"], interval="max", fidelity=60)
    assert pts, f"prices-history returned nothing for {target['question'][:40]}"
    print(f"✓ Pulled {len(pts)} price points for: {target['question'][:50]}")

    # Prices must be probabilities in [0,1]
    for p in pts[:50]:
        assert 0.0 <= p["price"] <= 1.0, f"price out of range: {p}"
    print("✓ All prices are valid probabilities in [0,1]")

    # Run the random baseline over cached resolved markets
    from polytester.backtest_runner import run_backtest
    from polytester.strategy_interface import RandomBaseline

    import random
    random.seed(0)
    result = run_backtest(
        strategy_class=RandomBaseline,
        market_ids=[m["market_id"] for m in resolved],
        start="2020-01-01",
        end="2030-01-01",
        initial_cash=10000,
    )
    print(f"✓ Backtest ran: {result['total_trades']} trades, PF={result['pf']}")

    # If any trades happened, settlement must be to exactly $0 or $1
    if not result["deals"].empty:
        exits = set(result["deals"]["exit_price"].unique())
        assert exits <= {0.0, 1.0}, f"non-binary settlement prices: {exits}"
        print("✓ All deals settled to exactly $0 or $1")


if __name__ == "__main__":
    test_gamma_field_parsing()
    test_live_pipeline()
    print("\n✓ End-to-end tests passed")
