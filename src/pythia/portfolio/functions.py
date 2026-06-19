"""Pure capital-state math (§4.4.1). Available capital is recomputed from the ledger, never static.

No clock, no I/O — a thin service reads the ledger and supplies realized PnL and locked capital.
"""


def available_capital(
    starting_bankroll: float, realized_pnl: float, locked_in_open: float
) -> float:
    return starting_bankroll + realized_pnl - locked_in_open


def exposure_fraction(deployed: float, capital: float) -> float:
    return deployed / capital if capital > 0.0 else 1.0
