"""Strategy base class and order types."""
from dataclasses import dataclass
from typing import Dict, List, Optional
import random


@dataclass
class Order:
    """Market order or limit order."""
    outcome: str  # 'YES', 'NO', or outcome name
    side: str  # 'BUY' or 'SELL'
    size: float  # position size at current price
    price: Optional[float] = None  # limit price; None = market order


@dataclass
class MarketState:
    """Current snapshot of market state."""
    timestamp: int  # unix epoch seconds
    market_id: str
    outcomes: List[str]  # e.g. ['YES', 'NO']
    prices: Dict[str, float]  # mid-price per outcome
    last_trade: Optional[Dict]  # {'price': 0.65, 'size': 10, 'timestamp': ...}
    is_open: bool  # market still accepting orders


class BaseStrategy:
    """Base class for backtest strategies."""

    def __init__(self, market_id: str, start_cash: float = 1000):
        self.market_id = market_id
        self.start_cash = start_cash
        self.cash = start_cash

    def on_market_state(self, market_state: MarketState, positions: Dict) -> List[Order]:
        """
        Called on every market tick.

        Args:
            market_state: current market state
            positions: {outcome: {'size': float, 'entry_price': float, 'current_mark': float}}

        Returns:
            List of Order objects (can be empty).
        """
        raise NotImplementedError


# Sizing guards. `size = notional / price` is UNBOUNDED as price -> 0: measured
# 2026-08-05, a market quoting 0.001 produced a 200,000-share position from a $200
# stake, and 15 of 106 random entries landed below 0.05 with a mean size of 15,365
# shares. That turns the "zero-edge" baseline into a lottery — its 8-seed PF band
# blew out to [0.36, 7.47] against a documented 0.83-0.97 — because PF is then
# decided by whether one longshot happened to win.
# Same defect class as HARNESS_GUIDE §4 on the MT5 side: floor the denominator and
# cap the size, or risk-based sizing detonates when the denominator collapses.
MIN_ENTRY_PRICE = 0.02   # below 2c the modeled spread rivals the contract price
MAX_SHARES = 10_000      # hard cap regardless of price


class RandomBaseline(BaseStrategy):
    """Random 50/50 entry on YES/NO; hold until resolution. Zero-edge baseline."""

    def __init__(self, market_id: str, start_cash: float = 1000,
                 notional: float = 100.0, min_entry_price: float = MIN_ENTRY_PRICE,
                 max_shares: float = MAX_SHARES):
        super().__init__(market_id, start_cash)
        self.entered = False
        self.notional = notional
        self.min_entry_price = min_entry_price
        self.max_shares = max_shares

    def on_market_state(self, market_state: MarketState, positions: Dict) -> List[Order]:
        if not self.entered and market_state['is_open']:
            # First tick: enter 50/50 random
            outcome = random.choice(market_state['outcomes'])
            price = market_state['prices'][outcome]

            # Skip degenerate quotes rather than taking a 1000x lottery ticket.
            # Marking entered=True means we do not retry later in the same market,
            # which keeps the trade count deterministic across seeds.
            self.entered = True
            if not price or price < self.min_entry_price or price > 1 - self.min_entry_price:
                return []

            size = min(self.notional / price, self.max_shares)
            return [Order(outcome=outcome, side='BUY', size=size, price=None)]

        return []
