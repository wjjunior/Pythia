"""Pure pricing for the paper ledger (§1.2, §4.3): cost-modeled fill price and PnL.

Honest cost modeling: a fill crosses the spread and pays fee + slippage, so the recorded entry is
worse than the observed mid. PnL treats `size` as the dollar stake on a binary that pays $1 — a win
returns the stake scaled by the entry odds, a loss forfeits it.
"""

from pythia.common import Side


def fill_price(
    observed_yes_price: float, side: Side, spread: float, *, fee: float, slippage: float
) -> float:
    base = observed_yes_price if side == Side.YES else 1.0 - observed_yes_price
    return min(1.0, max(0.0, base + spread / 2 + fee + slippage))


def realize_pnl(side: Side, entry_price: float, size: float, outcome: int) -> float:
    won = (side == Side.YES and outcome == 1) or (side == Side.NO and outcome == 0)
    if not won:
        return -size
    if entry_price <= 0.0:
        return 0.0
    return size * (1.0 - entry_price) / entry_price
