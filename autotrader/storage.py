"""Atomic local JSON persistence for the isolated paper portfolio; no credentials."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from decimal import Decimal
from .portfolio import PaperPortfolio, Position


def tail_jsonl(path, limit=50, *, block_size=65536):
    """Return the last ``limit`` JSON-object lines without reading the whole file.

    Run logs on the Railway volume are append-only and grow without bound, so
    status endpoints and the learning loop must not re-read them from the
    start on every request/scan. Unparseable lines (for example a line still
    being appended) are skipped, matching the previous full-read behavior.
    """
    limit = max(1, int(limit))
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []

    rows = []

    def _parse(raw):
        try:
            value = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return
        if isinstance(value, dict):
            rows.append(value)

    try:
        with target.open("rb") as handle:
            pos = size
            carry = b""
            while pos > 0 and len(rows) < limit:
                step = min(int(block_size), pos)
                pos -= step
                handle.seek(pos)
                chunk = handle.read(step) + carry
                lines = chunk.split(b"\n")
                # The first piece may be the tail of an earlier line unless we
                # reached the start of the file.
                carry = lines[0]
                for line in reversed(lines[1:]):
                    if line.strip():
                        _parse(line)
                        if len(rows) >= limit:
                            break
            if pos == 0 and len(rows) < limit and carry.strip():
                _parse(carry)
    except OSError:
        return []
    rows.reverse()
    return rows[-limit:]


def save_portfolio(portfolio: PaperPortfolio, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1, "cash": str(portfolio.cash),
        "realized_pnl": str(portfolio.realized_pnl),
        "max_position_fraction": str(portfolio.max_position_fraction),
        "max_total_fraction": str(portfolio.max_total_fraction),
        "positions": [dict(
            ticker=p.ticker, side=p.side, contracts=p.contracts,
            entry=str(p.entry), entry_fee=str(p.entry_fee),
            exposure_key=p.exposure_key, fair_snapshot_id=p.fair_snapshot_id,
            thesis=getattr(p, "thesis", None),
        ) for p in portfolio.positions.values()],
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
        p = Position(
            raw["ticker"], raw["side"], int(raw["contracts"]),
            Decimal(raw["entry"]), Decimal(raw["entry_fee"]),
            raw.get("exposure_key"), raw.get("fair_snapshot_id"),
            raw.get("thesis") if isinstance(raw.get("thesis"), dict) else None,
        )
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
