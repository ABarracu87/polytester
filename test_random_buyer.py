"""Test random buyer strategy against mock data."""
import json
from datetime import datetime
from polytester.simulator import Simulator
from polytester.strategies.random_buyer import RandomBuyer


def test_random_buyer():
    """Run random buyer on synthetic market data."""

    # Mock market trades: simulate 5 ticks
    market_id = "test_market"
    outcomes = ["YES", "NO"]
    end_time = 1704067200  # 2024-01-01 00:00:00

    trades = [
        {'outcome': 'YES', 'price': 0.50, 'size': 100, 'timestamp': 1704067100},
        {'outcome': 'YES', 'price': 0.52, 'size': 50, 'timestamp': 1704067110},
        {'outcome': 'YES', 'price': 0.55, 'size': 200, 'timestamp': 1704067120},
        {'outcome': 'NO', 'price': 0.48, 'size': 150, 'timestamp': 1704067130},
        {'outcome': 'YES', 'price': 0.60, 'size': 100, 'timestamp': 1704067140},
    ]

    # Run strategy
    strategy = RandomBuyer(market_id, start_cash=1000)
    sim = Simulator(strategy, market_id, initial_cash=1000)

    sim.run(
        trades,
        {
            'outcomes': outcomes,
            'resolved_outcome': 'YES',  # YES wins
            'end_time': end_time,
        },
        end_time
    )

    # Get results
    results = sim.get_results()

    print(f"\nRandom Buyer Test Results")
    print(f"=" * 40)
    print(f"Total trades: {results['total_trades']}")
    print(f"Final balance: ${results['final_balance']:.2f}")
    print(f"PF: {results['pf']}")
    print(f"Win rate: {results['win_rate']}")
    print(f"\nDeals:")
    if not results['deals'].empty:
        print(results['deals'].to_string())
    else:
        print("No deals closed")

    # Expect:
    # - 1 entry (first tick)
    # - 1 settlement (at resolution)
    # - Random outcome (50/50 YES/NO)
    assert results['total_trades'] == 1, f"Expected 1 trade, got {results['total_trades']}"

    # Check that the trade settled to either $0 or $1
    deal = results['deals'].iloc[0]
    assert deal['exit_price'] in [0.0, 1.0], f"Invalid exit price: {deal['exit_price']}"

    # If bought YES and YES won: profit. If bought NO and YES won: loss.
    if deal['outcome'] == 'YES':
        expected_pnl = (1.0 - deal['entry_price']) * deal['size']
        assert deal['pnl'] > 0, "Bought YES, YES won, should profit"
    else:
        expected_pnl = (0.0 - deal['entry_price']) * deal['size']
        assert deal['pnl'] < 0, "Bought NO, YES won, should lose"

    print("\n✓ Test passed")


if __name__ == "__main__":
    test_random_buyer()
