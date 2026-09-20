from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import signal_tracker


def main() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "signals.csv"
        market = {"ticker": "KXMLBGAME-TEST", "title": "Test MLB market"}
        fair = {
            "event_id": "evt-1",
            "sport_key": "baseball_mlb",
            "sport_title": "MLB",
            "market_type": "h2h",
            "fair_yes_prob": 0.61,
            "n_books": 5,
            "dispersion": 0.02,
        }
        q = SimpleNamespace(
            side="YES",
            execution="MAKE",
            recommended_limit=0.50,
            hard_max=0.52,
            net_edge=0.09,
            pmedge_risk_multiplier=0.5,
            pmedge_audit_bucket="mlb",
        )
        first = signal_tracker.record_signal(
            market,
            fair,
            q,
            lane="primary",
            source_version=27,
            path=path,
            alerted_at="2026-09-20T01:00:00Z",
        )
        assert first["recorded"] is True

        duplicate = signal_tracker.record_signal(
            market,
            fair,
            q,
            lane="primary",
            source_version=27,
            path=path,
            alerted_at="2026-09-20T01:05:00Z",
        )
        assert duplicate["recorded"] is False
        assert duplicate["reason"] == "duplicate"

        with path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        row = rows[0]
        assert row["ticker"] == "KXMLBGAME-TEST"
        assert row["lane"] == "primary"
        assert row["net_edge"] == "0.09"
        assert row["books"] == "5"
        assert row["risk_multiplier"] == "0.5"
        assert row["audit_bucket"] == "mlb"
        assert row["source_version"] == "27"

    print("PM Edge signal tracker tests: PASS")


if __name__ == "__main__":
    main()
