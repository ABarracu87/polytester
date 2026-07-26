"""Simple example strategy: buy YES/NO 50/50 during market hours.

This is the tutorial template — the smallest possible strategy, meant to be
read and copied. It is functionally identical to `RandomBaseline` in
strategy_interface.py (which the analyzer/runner import as the methodology's
zero-edge baseline); this copy lives in strategies/ so you have a standalone
file to base your own strategy on.
"""
import random
from polytester.strategy_interface import BaseStrategy, Order


class RandomBuyer(BaseStrategy):
    """
    Opens a position on first tick: 50/50 random YES or NO.
    Holds until market resolution. See module docstring re: RandomBaseline.
    """

    def __init__(self, market_id: str, start_cash: float = 1000):
        super().__init__(market_id, start_cash)
        self.entered = False

    def on_market_state(self, market_state, positions):
        """Open position on first market tick."""
        if not self.entered and market_state['is_open']:
            outcome = random.choice(market_state['outcomes'])
            price = market_state['prices'][outcome]
            # Buy $100 notional
            size = 100.0 / price if price > 0 else 0

            self.entered = True
            return [Order(outcome=outcome, side='BUY', size=size, price=None)]

        return []
