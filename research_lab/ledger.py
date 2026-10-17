"""Per-strategy paper ledgers.

Storage choice (free prototype): plain CSV/JSON files under research_lab/data/<strategy>/,
committed to Git by the Research Lab job only. Safeguards:

* every file is written to a temp file and atomically renamed, so a crash never leaves a
  half-written ledger;
* a lock file stops two runs on one machine; the GitHub workflow's concurrency group stops
  two runs at once across machines;
* position ids are deterministic (strategy, ticker, side, 30-minute run bucket), so a
  re-run of the same bucket cannot open a duplicate;
* only the Research Lab job writes these files, so its commits never collide with the
  production ledgers;
* a ledger that fails to parse stops the run instead of being overwritten.

Limits: a run whose commit never lands is lost (its paper trades vanish, they are not
duplicated). This is research-grade, not a financial system of record. A hosted database
(e.g. Supabase free tier) would remove that gap and needs a secret added to the repo.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research_lab.config import DATA_DIR

POSITION_FIELDS = [
    "position_id", "strategy", "version", "opened_at", "day", "ticker", "event_ticker", "title",
    "category", "market_type", "side", "contracts", "wanted", "entry_price", "cost", "fee", "committed",
    "fill_status", "fair", "fair_proportional", "fair_shin", "net_edge", "reason", "quote_at", "fair_at",
    "commence_time", "close_time", "details", "legs", "status", "result", "settled_at", "payout", "pl",
    "last_mid", "last_mid_at", "closing_mid", "clv_cents",
]
CANDIDATE_FIELDS = [
    "run_at", "strategy", "ticker", "event_ticker", "title", "category", "side", "decision", "reason",
    "fair", "ask", "net_edge", "quote_age_s", "fair_age_s", "spread", "details",
]
CANDIDATE_KEEP_DAYS = 14


class LedgerError(RuntimeError):
    pass


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    return "" if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        return None


def run_bucket(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return iso(dt.replace(minute=(dt.minute // 30) * 30, second=0, microsecond=0))


def position_id(strategy_key: str, ticker: str, side: str, when: datetime) -> str:
    return hashlib.sha1(f"{strategy_key}|{ticker}|{side}|{run_bucket(when)}".encode()).hexdigest()[:16]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_csv(path: Path, fields: list[str]) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames and "position_id" in fields and "position_id" not in reader.fieldnames:
                raise LedgerError(f"{path} has an unexpected header")
            return [dict(r) for r in reader]
    except (csv.Error, UnicodeDecodeError) as exc:
        raise LedgerError(f"{path} could not be parsed: {exc}") from exc


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    _atomic_write_text(path, buf.getvalue())


class Store:
    """One strategy's paper ledger: positions, recent candidates, and run stats."""

    def __init__(self, strategy_key: str, base: Path | None = None):
        self.key = strategy_key
        self.dir = Path(base or DATA_DIR) / strategy_key
        self.positions_path = self.dir / "positions.csv"
        self.candidates_path = self.dir / "candidates.csv"
        self.stats_path = self.dir / "stats.json"
        self.positions: list[dict] = _read_csv(self.positions_path, POSITION_FIELDS)
        self.candidates: list[dict] = _read_csv(self.candidates_path, CANDIDATE_FIELDS)
        self.stats: dict = {}
        if self.stats_path.exists():
            try:
                self.stats = json.loads(self.stats_path.read_text() or "{}")
            except ValueError as exc:
                raise LedgerError(f"{self.stats_path} could not be parsed") from exc
        self.stats.setdefault("counters", {})
        self.stats.setdefault("rejections", {})
        self.stats.setdefault("runs", [])

    # ---- positions ----
    def has_position(self, pid: str) -> bool:
        return any(p.get("position_id") == pid for p in self.positions)

    def add_position(self, row: dict) -> bool:
        if self.has_position(row["position_id"]):
            return False
        self.positions.append(row)
        return True

    def open_positions(self) -> list[dict]:
        return [p for p in self.positions if p.get("status") == "open"]

    # ---- candidates / counters ----
    def log_candidate(self, row: dict) -> None:
        self.candidates.append(row)
        decision = str(row.get("decision") or "")
        self.bump(f"decision_{decision.lower()}")
        if decision == "REJECTED":
            reason = str(row.get("reason") or "unknown")
            self.stats["rejections"][reason] = int(self.stats["rejections"].get(reason, 0)) + 1

    def bump(self, name: str, n: int = 1) -> None:
        c = self.stats["counters"]
        c[name] = int(c.get(name, 0)) + n

    def record_run(self, when: datetime, ok: bool, info: dict) -> None:
        entry = {"at": iso(when), "ok": bool(ok), **info}
        self.stats["runs"] = (self.stats["runs"] + [entry])[-200:]
        if ok:
            self.stats["last_success"] = iso(when)
        else:
            self.stats["last_failure"] = iso(when)

    def save(self, now: datetime | None = None) -> None:
        now = now or now_utc()
        cutoff = now - timedelta(days=CANDIDATE_KEEP_DAYS)
        self.candidates = [c for c in self.candidates if (parse_dt(c.get("run_at")) or now) >= cutoff]
        _write_csv(self.positions_path, self.positions, POSITION_FIELDS)
        _write_csv(self.candidates_path, self.candidates, CANDIDATE_FIELDS)
        _atomic_write_text(self.stats_path, json.dumps(self.stats, indent=1, sort_keys=True, default=str))


@contextlib.contextmanager
def run_lock(base: Path | None = None):
    """Exclusive lock for one Research Lab run on this machine. Raises if already held."""
    import fcntl
    d = Path(base or DATA_DIR)
    d.mkdir(parents=True, exist_ok=True)
    fh = (d / ".run.lock").open("w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LedgerError("another Research Lab run is in progress") from exc
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def dumps(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, default=str)


def loads(text, default=None):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default
