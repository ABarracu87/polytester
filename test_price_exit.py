"""Test mid-market price exit: close a position on take-profit BEFORE resolution."""
from polytester.simulator import Simulator
from polytester.strategies.price_trader import PriceTrader
from polytester.strategy_interface import BaseStrategy, Order


def test_take_profit_exit():
    """YES dips to 0.35 (buy), rises to 0.45 (take-profit +0.08 hits) -> exit
    mid-market at ~0.45, NOT at resolution. Even though YES ultimately LOSES
    (resolves NO), the trade is profitable because we sold before settlement."""
    # YES stays above entry_below after the TP so there's no re-entry;
    # isolates the single mid-market exit.
    end_time = 2000
    ticks = [
        {'outcome': 'Yes', 'price': 0.35, 'size': 1, 'timestamp': 1100},  # entry
        {'outcome': 'Yes', 'price': 0.40, 'size': 1, 'timestamp': 1200},
        {'outcome': 'Yes', 'price': 0.45, 'size': 1, 'timestamp': 1300},  # TP hits (+0.10)
        {'outcome': 'Yes', 'price': 0.55, 'size': 1, 'timestamp': 1400},  # stays > entry_below, no re-entry
    ]

    strat = PriceTrader('m', 1000, entry_below=0.40, take_profit=0.08, stop_loss=0.08)
    sim = Simulator(strat, 'm', 1000, bid_ask_cents=0.0)  # no spread, clean check
    sim.run(ticks, {'outcomes': ['Yes', 'No'], 'resolved_outcome': 'No',
                    'end_time': end_time}, end_time)
    r = sim.get_results()

    print("\nTake-profit exit test")
    print(r['deals'][['outcome', 'side', 'entry_price', 'exit_price', 'size', 'pnl', 'exit_reason']].to_string())

    assert r['total_trades'] == 1, f"expected 1 closed deal, got {r['total_trades']}"
    deal = r['deals'].iloc[0]
    assert deal['exit_reason'] == 'exit', f"should exit mid-market, got {deal['exit_reason']}"
    assert abs(deal['exit_price'] - 0.45) < 1e-9, f"should exit at 0.45, got {deal['exit_price']}"
    assert deal['pnl'] > 0, f"take-profit trade should be positive, got {deal['pnl']}"
    # YES resolved NO ($0), but we sold at 0.45 first -> profit despite being "wrong"
    print("✓ Closed at 0.45 mid-market (before resolution), profit realized despite YES losing")


def test_no_double_count_at_resolution():
    """A position closed mid-market must NOT be re-settled at resolution."""
    end_time = 2000
    ticks = [
        {'outcome': 'Yes', 'price': 0.30, 'size': 1, 'timestamp': 1100},
        {'outcome': 'Yes', 'price': 0.42, 'size': 1, 'timestamp': 1200},  # TP -> exit
        {'outcome': 'Yes', 'price': 0.90, 'size': 1, 'timestamp': 1300},
    ]
    strat = PriceTrader('m', 1000, entry_below=0.35, take_profit=0.08, stop_loss=0.5)
    sim = Simulator(strat, 'm', 1000, bid_ask_cents=0.0)
    sim.run(ticks, {'outcomes': ['Yes', 'No'], 'resolved_outcome': 'Yes',
                    'end_time': end_time}, end_time)
    r = sim.get_results()

    assert r['total_trades'] == 1, f"exactly one deal expected, got {r['total_trades']}"
    assert r['deals'].iloc[0]['exit_reason'] == 'exit'
    print("✓ Mid-market exit not double-settled at resolution")


def test_ride_to_resolution():
    """If TP/SL never hit, the position rides into resolution (settle $0/$1)."""
    end_time = 2000
    ticks = [
        {'outcome': 'Yes', 'price': 0.38, 'size': 1, 'timestamp': 1100},  # entry
        {'outcome': 'Yes', 'price': 0.39, 'size': 1, 'timestamp': 1200},  # no TP/SL
    ]
    strat = PriceTrader('m', 1000, entry_below=0.40, take_profit=0.20, stop_loss=0.20)
    sim = Simulator(strat, 'm', 1000, bid_ask_cents=0.0)
    sim.run(ticks, {'outcomes': ['Yes', 'No'], 'resolved_outcome': 'Yes',
                    'end_time': end_time}, end_time)
    r = sim.get_results()

    deal = r['deals'].iloc[0]
    assert deal['exit_reason'] == 'settle', f"should settle, got {deal['exit_reason']}"
    assert deal['exit_price'] == 1.0, "Yes won -> settle at 1.0"
    print("✓ No-signal position rides to resolution and settles at $1")


if __name__ == "__main__":
    test_take_profit_exit()
    test_no_double_count_at_resolution()
    test_ride_to_resolution()
    print("\n✓ All mid-market exit tests passed")
