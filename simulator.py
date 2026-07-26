"""Event-driven backtest simulator."""
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd
from dataclasses import dataclass


@dataclass
class Position:
    outcome: str
    side: str  # 'BUY' or 'SELL'
    size: float
    entry_price: float
    entry_ts: int


class Simulator:
    """Event-replay simulator for Polymarket strategies."""

    def __init__(self, strategy, market_id: str, initial_cash: float, bid_ask_cents: float = 0.5):
        self.strategy = strategy
        self.market_id = market_id
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {}  # outcome -> Position
        self.bid_ask = bid_ask_cents / 100
        self.deals = []  # List of closed trades
        self.equity_history = [(None, initial_cash)]

    def run(self, trades: List[Dict], market_metadata: Dict, end_time: int):
        """
        Replay trades chronologically; call strategy on each tick.

        Args:
            trades: sorted list of {'outcome', 'price', 'size', 'timestamp', ...}
            market_metadata: {'outcomes': [...], 'end_time': int}
            end_time: unix timestamp of market resolution
        """
        if not trades:
            return

        outcomes = market_metadata.get('outcomes', ['YES', 'NO'])
        prices = {o: 0.5 for o in outcomes}  # Default; updated by trades

        for i, trade in enumerate(trades):
            ts = trade['timestamp']
            outcome = trade['outcome']
            price = trade['price']

            # Update price
            prices[outcome] = price

            # Compute mid from trade
            market_state = {
                'timestamp': ts,
                'market_id': self.market_id,
                'outcomes': outcomes,
                'prices': prices,
                'last_trade': {'price': price, 'size': trade['size'], 'timestamp': ts},
                'is_open': ts < end_time,
            }

            # Get orders from strategy
            positions_snapshot = {o: {
                'size': (self.positions.get(o, Position(o, '', 0, 0, 0)).size),
                'entry_price': (self.positions.get(o, Position(o, '', 0, 0, 0)).entry_price),
                'current_mark': prices.get(o, 0.5),
            } for o in outcomes}

            orders = self.strategy.on_market_state(market_state, positions_snapshot)

            # Fill orders
            for order in orders:
                self._fill_order(order, price, ts)

            # Mark-to-market
            self._mark_positions(prices, ts)

        # Settle at resolution
        if 'resolved_outcome' in market_metadata:
            self._settle(market_metadata['resolved_outcome'], end_time)

    def _fill_order(self, order, current_price: float, ts: int):
        """Fill an order at current price + slippage.

        An order OPPOSITE to an existing position on the same outcome CLOSES
        (nets) it at the current price and books a deal — this is how a strategy
        exits mid-market instead of holding to resolution. Same-direction order
        adds to the position. Mirrors real prediction-market mechanics: selling
        the YES token you hold realizes PnL now; it does not open a short.
        """
        outcome = order.outcome
        size = order.size
        side = order.side

        # Slipped fill price
        fill_price = current_price + self.bid_ask if side == 'BUY' else current_price - self.bid_ask

        existing = self.positions.get(outcome)

        # OPPOSITE side => close/reduce the existing position at fill_price
        if existing is not None and existing.side != side:
            self._close_position(outcome, fill_price, ts, close_size=size,
                                  exit_reason='exit')
            return

        # SAME side (or new) => open/add. Enforce cash for the added leg.
        cost = size * fill_price
        if cost > self.cash:
            return  # insufficient cash
        self.cash -= cost

        if existing is None:
            self.positions[outcome] = Position(outcome, side, size, fill_price, ts)
        else:
            new_size = existing.size + size
            existing.entry_price = (existing.size * existing.entry_price + size * fill_price) / new_size
            existing.size = new_size

    def _close_position(self, outcome: str, exit_price: float, ts: int,
                        close_size: Optional[float] = None, exit_reason: str = 'exit'):
        """Close (or partially close) a position at exit_price and book a deal."""
        pos = self.positions.get(outcome)
        if pos is None:
            return

        qty = pos.size if close_size is None else min(close_size, pos.size)

        if pos.side == 'BUY':
            pnl = (exit_price - pos.entry_price) * qty
            self.cash += qty * exit_price               # sell the tokens back
        else:  # SELL
            pnl = (pos.entry_price - exit_price) * qty
            self.cash += qty * (1.0 - exit_price)       # release short collateral

        self.deals.append({
            'outcome': outcome,
            'side': pos.side,
            'entry_price': pos.entry_price,
            'exit_price': exit_price,
            'size': qty,
            'pnl': pnl,
            'entry_ts': pos.entry_ts,
            'exit_ts': ts,
            'exit_reason': exit_reason,
        })

        if qty >= pos.size:
            del self.positions[outcome]
        else:
            pos.size -= qty

    def _mark_positions(self, prices: Dict[str, float], ts: int):
        """Mark positions to market."""
        for outcome, pos in self.positions.items():
            if outcome in prices:
                # Update equity
                if pos.side == 'BUY':
                    pnl = (prices[outcome] - pos.entry_price) * pos.size
                else:  # SELL
                    pnl = (pos.entry_price - prices[outcome]) * pos.size
                self.equity_history.append((ts, self.cash + pnl))

    def _settle(self, resolved_outcome: str, ts: int):
        """Settle any positions still open at resolution: winner $1, loser $0.
        Anything the strategy already exited mid-market is gone from
        self.positions and is not re-settled."""
        for outcome, pos in list(self.positions.items()):
            settle_price = 1.0 if outcome == resolved_outcome else 0.0
            self._close_position(outcome, settle_price, ts, exit_reason='settle')

    def get_results(self) -> Dict:
        """Compute PF, DD, Sharpe."""
        deals_df = pd.DataFrame(self.deals)

        if deals_df.empty:
            return {
                'pf': 0,
                'max_dd': 0,
                'sharpe': 0,
                'total_trades': 0,
                'win_rate': 0,
                'final_balance': self.cash,
                'deals': deals_df,
            }

        gross_profit = deals_df[deals_df['pnl'] > 0]['pnl'].sum()
        gross_loss = -deals_df[deals_df['pnl'] < 0]['pnl'].sum()

        pf = gross_profit / gross_loss if gross_loss > 0 else 0
        win_rate = (deals_df['pnl'] > 0).sum() / len(deals_df) if len(deals_df) > 0 else 0

        # Max DD
        equity = [self.initial_cash] + [self.cash + d['pnl'] for _, d in deals_df.iterrows()]
        running_max = equity[0]
        max_dd = 0
        for e in equity:
            running_max = max(running_max, e)
            dd = (running_max - e) / running_max if running_max > 0 else 0
            max_dd = max(max_dd, dd)

        # Sharpe
        returns = deals_df['pnl'] / (deals_df['size'] * deals_df['entry_price'])
        sharpe = (returns.mean() / returns.std() * (252**0.5)) if returns.std() > 0 else 0

        return {
            'pf': round(pf, 2),
            'max_dd': round(max_dd, 3),
            'sharpe': round(sharpe, 2),
            'total_trades': len(deals_df),
            'win_rate': round(win_rate, 2),
            'final_balance': round(self.cash, 2),
            'deals': deals_df,
        }
