"""Atomic local JSON persistence for the isolated paper portfolio; no credentials."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from decimal import Decimal
from .portfolio import PaperPortfolio, Position


def save_portfolio(portfolio: PaperPortfolio, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1, "cash": str(portfolio.cash),
        "realized_pnl": str(portfolio.realized_pnl),
        "max_position_fraction": str(portfolio.max_position_fraction),
        "max_total_fraction": str(portfolio.max_total_fraction),
        "positions": [dict(ticker=p.ticker, side=p.side, contracts=p.contracts,
                           entry=str(p.entry), entry_fee=str(p.entry_fee))
                      for p in portfolio.positions.values()],
        "ledger": portfolio.ledger,
    }
    tmp = None
    try:
        with NamedTemporaryFile(mode="w", dir=target.parent, prefix=".paper-",
                                suffix=".tmp", delete=False, encoding="utf-8") as handle:
            tmp = Path(handle.name)
            json.dump(payload, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def load_portfolio(path):
    target = Path(path)
    if not target.exists():
        return PaperPortfolio()
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("Unsupported paper portfolio schema")
    positions = {}
    for raw in data["positions"]:
        p = Position(raw["ticker"], raw["side"], int(raw["contracts"]),
                     Decimal(raw["entry"]), Decimal(raw["entry_fee"]))
        if p.ticker in positions or p.side not in ("YES", "NO") or p.contracts < 1:
            raise ValueError("Invalid stored paper position")
        positions[p.ticker] = p
    portfolio = PaperPortfolio(cash=Decimal(data["cash"]), positions=positions,
                               realized_pnl=Decimal(data["realized_pnl"]),
                               ledger=data["ledger"],
                               max_position_fraction=Decimal(data["max_position_fraction"]),
                               max_total_fraction=Decimal(data["max_total_fraction"]))
    if portfolio.cash < 0 or portfolio.max_position_fraction <= 0 or portfolio.max_total_fraction <= 0:
        raise ValueError("Invalid stored paper portfolio")
    return portfolio
