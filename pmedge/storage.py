from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from .utils import utc_now_iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  ticker TEXT NOT NULL,
  event_ticker TEXT,
  title TEXT,
  market_type TEXT,
  selection TEXT,
  point REAL,
  fair_yes_prob REAL,
  fair_source TEXT,
  fair_confidence REAL,
  match_confidence REAL,
  side TEXT,
  execution TEXT,
  entry_price REAL,
  hard_max REAL,
  net_edge REAL,
  contracts INTEGER,
  est_cost REAL,
  status TEXT DEFAULT 'SIGNAL',
  extra_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);

CREATE TABLE IF NOT EXISTS paper_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  ticker TEXT NOT NULL,
  side TEXT NOT NULL,
  execution TEXT NOT NULL,
  limit_price REAL NOT NULL,
  contracts INTEGER NOT NULL,
  fee_per_contract REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN',
  fill_price REAL,
  filled_at TEXT,
  resolved_result TEXT,
  resolved_at TEXT,
  pnl REAL,
  FOREIGN KEY(signal_id) REFERENCES signals(id)
);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_orders(status);

CREATE TABLE IF NOT EXISTS live_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id INTEGER,
  created_at TEXT NOT NULL,
  ticker TEXT NOT NULL,
  kalshi_order_id TEXT,
  client_order_id TEXT,
  side TEXT,
  book_side TEXT,
  submitted_price REAL,
  contracts REAL,
  response_json TEXT
);
"""


class Store:
    def __init__(self, path: str):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(p)
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def add_signal(self, row: dict) -> int:
        fields = [
            "created_at","ticker","event_ticker","title","market_type","selection","point",
            "fair_yes_prob","fair_source","fair_confidence","match_confidence","side","execution",
            "entry_price","hard_max","net_edge","contracts","est_cost","status","extra_json"
        ]
        data = dict(row)
        data.setdefault("created_at", utc_now_iso())
        data.setdefault("status", "SIGNAL")
        data["extra_json"] = json.dumps(data.get("extra_json") or {}, default=str)
        vals = [data.get(f) for f in fields]
        with self.conn() as c:
            cur = c.execute(
                f"INSERT INTO signals ({','.join(fields)}) VALUES ({','.join(['?']*len(fields))})", vals
            )
            return int(cur.lastrowid)

    def add_paper_order(self, signal_id: int, row: dict) -> int:
        with self.conn() as c:
            cur = c.execute(
                """INSERT INTO paper_orders
                (signal_id,created_at,ticker,side,execution,limit_price,contracts,fee_per_contract,status)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    signal_id, utc_now_iso(), row["ticker"], row["side"], row["execution"],
                    row["entry_price"], row["contracts"], row["fee_per_contract"],
                    "FILLED" if row["execution"] == "TAKE" else "OPEN",
                ),
            )
            oid = int(cur.lastrowid)
            if row["execution"] == "TAKE":
                c.execute(
                    "UPDATE paper_orders SET fill_price=?, filled_at=? WHERE id=?",
                    (row["entry_price"], utc_now_iso(), oid),
                )
            return oid

    def open_paper_orders(self):
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM paper_orders WHERE status='OPEN'")]

    def mark_paper_filled(self, order_id: int, price: float):
        with self.conn() as c:
            c.execute(
                "UPDATE paper_orders SET status='FILLED', fill_price=?, filled_at=? WHERE id=? AND status='OPEN'",
                (price, utc_now_iso(), order_id),
            )

    def unresolved_filled_orders(self):
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM paper_orders WHERE status='FILLED' AND resolved_result IS NULL")]

    def resolve_order(self, order_id: int, result: str, pnl: float):
        with self.conn() as c:
            c.execute(
                "UPDATE paper_orders SET status='RESOLVED', resolved_result=?, resolved_at=?, pnl=? WHERE id=?",
                (result, utc_now_iso(), pnl, order_id),
            )

    def add_live_order(self, signal_id: int, row: dict, response: dict):
        with self.conn() as c:
            c.execute(
                """INSERT INTO live_orders
                (signal_id,created_at,ticker,kalshi_order_id,client_order_id,side,book_side,submitted_price,contracts,response_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    signal_id, utc_now_iso(), row["ticker"], response.get("order_id"),
                    response.get("client_order_id"), row["side"], row["book_side"],
                    row["submitted_price"], row["contracts"], json.dumps(response),
                ),
            )

    def analytics(self) -> dict:
        with self.conn() as c:
            resolved = [dict(r) for r in c.execute("SELECT * FROM paper_orders WHERE status='RESOLVED'")]
            signals = [dict(r) for r in c.execute("SELECT * FROM signals")]
        pnl = sum(float(r.get("pnl") or 0) for r in resolved)
        wins = sum(1 for r in resolved if float(r.get("pnl") or 0) > 0)
        return {
            "signals": len(signals),
            "resolved_orders": len(resolved),
            "wins": wins,
            "losses": len(resolved) - wins,
            "pnl": pnl,
            "win_rate": wins / len(resolved) if resolved else None,
        }

    def get_signal(self, signal_id: int):
        with self.conn() as c:
            r = c.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            return dict(r) if r else None

    def recent_signals(self, limit: int = 200):
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))]

    def recent_paper_orders(self, limit: int = 200):
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM paper_orders ORDER BY id DESC LIMIT ?", (limit,))]

    def has_active_paper_order(self, ticker: str, side: str) -> bool:
        with self.conn() as c:
            r = c.execute(
                "SELECT 1 FROM paper_orders WHERE ticker=? AND side=? AND status IN ('OPEN','FILLED') LIMIT 1",
                (ticker, side),
            ).fetchone()
            return r is not None

    def has_recent_signal(self, ticker: str, side: str, minutes: int = 30) -> bool:
        with self.conn() as c:
            r = c.execute(
                """SELECT 1 FROM signals
                   WHERE ticker=? AND side=?
                     AND datetime(created_at) >= datetime('now', ?)
                   LIMIT 1""",
                (ticker, side, f"-{int(minutes)} minutes"),
            ).fetchone()
            return r is not None

    def risk_cost_today(self) -> float:
        # Count only signals that became a paper or live order; merely scanning should not burn risk budget.
        with self.conn() as c:
            r = c.execute(
                """SELECT COALESCE(SUM(s.est_cost),0) AS x
                   FROM signals s
                   WHERE date(s.created_at)=date('now')
                     AND (EXISTS (SELECT 1 FROM paper_orders p WHERE p.signal_id=s.id)
                          OR EXISTS (SELECT 1 FROM live_orders l WHERE l.signal_id=s.id))"""
            ).fetchone()
            return float(r["x"] or 0.0)

    def risk_cost_event(self, event_ticker: str) -> float:
        if not event_ticker:
            return 0.0
        with self.conn() as c:
            r = c.execute(
                """SELECT COALESCE(SUM(s.est_cost),0) AS x
                   FROM signals s
                   WHERE s.event_ticker=?
                     AND (EXISTS (SELECT 1 FROM paper_orders p WHERE p.signal_id=s.id)
                          OR EXISTS (SELECT 1 FROM live_orders l WHERE l.signal_id=s.id))""",
                (event_ticker,),
            ).fetchone()
            return float(r["x"] or 0.0)
