"""Atomic local JSON persistence for the isolated paper portfolio; no credentials."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from decimal import Decimal
from .portfolio import PaperPortfolio, Position


def _archive_pattern(target):
    import re
    stem = re.escape(target.stem)
    return re.compile(rf"^{stem}\.(\d{{4}}-\d{{2}}-\d{{2}})(?:\.(\d+))?\.jsonl(\.gz)?$")


def jsonl_archives(path):
    """Archived day files for one run log, oldest first: [(date, seq, Path, gz)]."""
    target = Path(path)
    pattern = _archive_pattern(target)
    out = []
    try:
        names = os.listdir(target.parent)
    except OSError:
        return []
    for name in names:
        m = pattern.match(name)
        if m:
            out.append((m.group(1), int(m.group(2) or 0), target.parent / name, bool(m.group(3))))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def _gzip_atomic(src):
    import gzip
    import shutil
    dst = src.with_name(src.name + ".gz")
    tmp = None
    try:
        with NamedTemporaryFile(mode="wb", dir=src.parent, prefix=".rotate-", suffix=".gz.tmp",
                                delete=False) as raw:
            tmp = Path(raw.name)
            with gzip.GzipFile(fileobj=raw, mode="wb") as gz, src.open("rb") as handle:
                shutil.copyfileobj(handle, gz)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, dst)
        src.unlink()
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    return dst


def rotate_jsonl(path, *, now=None, retain_days=30):
    """Daily rotation for one lane's append-only run log.

    * The active file is renamed to ``<stem>.<YYYY-MM-DD>.jsonl`` once its last
      write is from an earlier UTC day (today's active log is never touched).
    * Only the newest completed day stays plain (cheap reverse tail reads);
      older plain archives are gzipped atomically.
    * Archives older than ``retain_days`` are deleted.
    Each lane has its own file name, so archives never mix lanes.
    """
    from datetime import datetime, timedelta, timezone
    target = Path(path)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now.date()
    result = {"rotated": None, "gzipped": [], "deleted": []}
    try:
        st = target.stat()
    except OSError:
        st = None
    if st is not None and st.st_size > 0:
        last_day = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).date()
        if last_day < today:
            day = last_day.isoformat()
            seq = 0
            while True:
                name = f"{target.stem}.{day}{'' if seq == 0 else '.' + str(seq)}.jsonl"
                dest = target.with_name(name)
                if not dest.exists() and not dest.with_name(name + ".gz").exists():
                    break
                seq += 1
            os.replace(target, dest)
            result["rotated"] = str(dest)
    archives = jsonl_archives(target)
    plain = [a for a in archives if not a[3]]
    for _day, _seq, apath, _gz in plain[:-1]:
        result["gzipped"].append(str(_gzip_atomic(apath)))
    cutoff = (today - timedelta(days=int(retain_days))).isoformat()
    for day, _seq, apath, _gz in jsonl_archives(target):
        if day < cutoff:
            try:
                apath.unlink()
                result["deleted"].append(str(apath))
            except OSError:
                pass
    return result


def append_jsonl(path, row, *, now=None, retain_days=None):
    """Rotate if a new UTC day started, then append one JSON line."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if retain_days is None:
        try:
            retain_days = int(os.environ.get("AUTOTRADER_RUNLOG_RETAIN_DAYS", "30"))
        except (TypeError, ValueError):
            retain_days = 30
    try:
        rotate_jsonl(target, now=now, retain_days=retain_days)
    except OSError:
        pass  # rotation is housekeeping; never lose the run record over it
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def _tail_one_file(path, limit, *, block_size=65536):
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


def _tail_gz(path, limit):
    import gzip
    from collections import deque
    rows = deque(maxlen=max(1, int(limit)))
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except Exception:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except (OSError, EOFError):
        return []
    return list(rows)


def tail_jsonl(path, limit=50, *, block_size=65536):
    """Return the last ``limit`` JSON-object lines of a (rotated) run log.

    Reads the active file backwards without loading it whole; if it holds
    fewer than ``limit`` rows (e.g. just after daily rotation) older day
    archives are consulted newest-first, so callers always see recent runs.
    """
    limit = max(1, int(limit))
    rows = _tail_one_file(path, limit, block_size=block_size)
    if len(rows) >= limit:
        return rows
    for _day, _seq, apath, gz in reversed(jsonl_archives(path)):
        need = limit - len(rows)
        older = _tail_gz(apath, need) if gz else _tail_one_file(apath, need, block_size=block_size)
        rows = older[-need:] + rows
        if len(rows) >= limit:
            break
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
