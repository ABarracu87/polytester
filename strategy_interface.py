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


class RandomBaseline(BaseStrategy):
    """Random 50/50 entry on YES/NO; hold until resolution. Zero-edge baseline."""

    def __init__(self, market_id: str, start_cash: float = 1000):
        super().__init__(market_id, start_cash)
        self.entered = False

    def on_market_state(self, market_state: MarketState, positions: Dict) -> List[Order]:
        if not self.entered and market_state['is_open']:
            # First tick: enter 50/50 random
            outcome = random.choice(market_state['outcomes'])
            size = 100 / market_state['prices'][outcome]  # $100 notional
            self.entered = True
            return [Order(outcome=outcome, side='BUY', size=size, price=None)]

        return []
