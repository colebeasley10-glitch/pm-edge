from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

import dashboard_data
import signal_outcome_tracker


def main() -> None:
    signals = pd.DataFrame([
        {
            "record_type":"signal","signal_id":"0123456789abcdefabcd",
            "alerted_at":"2026-09-21T12:00:00Z","trade_date":"2026-09-21",
            "ticker":"KXNFLGAME-E1-A","side":"YES","lane":"price_fish",
            "sport_title":"NFL","market_type":"h2h","market_title":"A wins",
            "execution":"MAKE","recommended_limit":0.40,"hard_max":0.40,
            "fair_probability":0.48,"net_edge":0.06,"books":5,"dispersion":0.01,
            "commence_time":"2026-09-21T15:00:00Z","source_version":28,
        }
    ])
    orders = pd.DataFrame([{
        "ticker":"KXNFLGAME-E1-A","side":"YES","lane":"approval_link",
        "status":"settled_win","filled_shares":5,"entry_cost":2.0,
        "net_pl":3.0,"client_order_id":"pmedge-0123456789abcdefabcd-a1b2c3d4",
    }])

    enriched = dashboard_data.enrich_orders(orders, signals)
    assert enriched.iloc[0]["sport_title"] == "NFL"
    assert enriched.iloc[0]["lane"] == "price_fish"
    assert enriched.iloc[0]["signal_id"] == "0123456789abcdefabcd"

    summary = dashboard_data.actual_summary(enriched)
    assert summary["record"] == "1-0"
    assert summary["net_pl"] == 3.0
    assert abs(summary["roi"] - 1.5) < 1e-9

    fresh = dashboard_data.new_signals(signals, pd.DataFrame(), now=datetime(2026,9,21,14,0,tzinfo=timezone.utc))
    assert len(fresh) == 1
    sized = dashboard_data.size_signal(signals.iloc[0].to_dict())
    assert sized and sized["max_loss"] <= 10.0

    # Whale signals may intentionally omit sportsbook fair-value metadata, but
    # the dashboard must still create a signed Review & Place link.
    previous_secret = os.environ.get("PM_EDGE_APPROVAL_SECRET")
    os.environ["PM_EDGE_APPROVAL_SECRET"] = "hub-test-secret"
    try:
        whale = {
            "signal_id": "8b33884ce96641c7cf27",
            "ticker": "KXUCLWTOTAL-26SEP23SEROL-8",
            "side": "YES",
            "lane": "whale",
            "execution": "TAKE",
            "recommended_limit": 0.79,
            "fair_probability": float("nan"),
            "net_edge": float("nan"),
            "books": float("nan"),
            "dispersion": float("nan"),
            "source_version": 30,
            "commence_time": "2026-09-25T16:45:00Z",
        }
        url = dashboard_data.approval_url_for_signal(
            whale, base_url="https://example.streamlit.app"
        )
        assert url and url.startswith("https://example.streamlit.app/Approve?trade=")
    finally:
        if previous_secret is None:
            os.environ.pop("PM_EDGE_APPROVAL_SECRET", None)
        else:
            os.environ["PM_EDGE_APPROVAL_SECRET"] = previous_secret

    with TemporaryDirectory() as td:
        root = Path(td)
        signal_path = root / "signals.csv"
        outcome_path = root / "outcomes.csv"
        signals.to_csv(signal_path, index=False)
        old_signal, old_outcome = signal_outcome_tracker.SIGNAL_LEDGER, signal_outcome_tracker.OUTCOME_LEDGER
        signal_outcome_tracker.SIGNAL_LEDGER, signal_outcome_tracker.OUTCOME_LEDGER = signal_path, outcome_path
        try:
            def loader(_ticker):
                return {"market_result":"yes","settled_time":"2026-09-21T18:00:00Z"}
            result = signal_outcome_tracker.track_signal_outcomes(
                now=datetime(2026,9,21,19,0,tzinfo=timezone.utc),
                market_loader=loader,
            )
        finally:
            signal_outcome_tracker.SIGNAL_LEDGER, signal_outcome_tracker.OUTCOME_LEDGER = old_signal, old_outcome
        assert result["settled"] == 1
        rows = pd.read_csv(outcome_path)
        assert rows.iloc[0]["signal_outcome"] == "WIN"
        assert rows.iloc[0]["hypothetical_gross_pl"] > 0

    print("PM Edge Hub MVP tests: PASS")


if __name__ == "__main__":
    main()
