"""Trade on price DURING the market, exiting before resolution.

Contrast with RandomBaseline (buy once, hold to $0/$1 settlement). This one
opens on a price signal and CLOSES at a take-profit or stop-loss price while
the market is still open — testing whether you can make money on price moves
rather than on being right about the final outcome.

Signal (deliberately simple, tune via params):
- Enter YES when its price dips below `entry_below` (betting on a bounce).
- Exit at `+take_profit` or `-stop_loss` from entry, whichever hits first.
- If neither hits, the position rides into resolution (settles $0/$1).
"""
from polytester.strategy_interface import BaseStrategy, Order


class PriceTrader(BaseStrategy):
    def __init__(self, market_id, start_cash=1000,
                 entry_below=0.40, take_profit=0.08, stop_loss=0.08,
                 notional=100.0):
        super().__init__(market_id, start_cash)
        self.entry_below = entry_below
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.notional = notional
        self.entry_price = None  # price we bought YES at

    def on_market_state(self, market_state, positions):
        outcomes = market_state['outcomes']
        if not outcomes:
            return []
        yes = outcomes[0]                      # first outcome = "Yes" side
        price = market_state['prices'].get(yes, 0.5)
        held = positions.get(yes, {}).get('size', 0) > 0

        # No position: look for an entry (only while market is open)
        if not held and market_state['is_open']:
            if price <= self.entry_below and price > 0:
                self.entry_price = price
                return [Order(outcome=yes, side='BUY',
                              size=self.notional / price, price=None)]
            return []

        # Holding: exit on take-profit or stop-loss (SELL nets the position)
        if held and self.entry_price is not None:
            if price >= self.entry_price + self.take_profit or \
               price <= self.entry_price - self.stop_loss:
                self.entry_price = None
                return [Order(outcome=yes, side='SELL',
                              size=positions[yes]['size'], price=None)]

        return []
