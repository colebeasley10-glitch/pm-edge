from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import execution_guard


NOW = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)
AFTER_TEMP_DAILY_CAP_TEST = datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc)


def stage(path: Path, ticker: str, *, risk: float = 3.78, price: float = 0.42, shares: int = 9):
    return execution_guard.stage_order_intent(
        ticker=ticker,
        market_title=f"Test market {ticker}",
        side="YES",
        lane="primary",
        limit_price=price,
        shares=shares,
        max_loss=risk,
        fair_probability=0.50,
        net_edge=0.061,
        books=6,
        source_version=23,
        path=path,
        now=NOW,
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "queue.json"

        first = stage(path, "TEST-1")
        assert first["staged"] is True
        assert first["reason"] == "awaiting_user_approval"

        duplicate = stage(path, "TEST-1")
        assert duplicate["staged"] is False
        assert duplicate["reason"] == "duplicate"

        payload = execution_guard._load_queue(path)
        assert len(payload["intents"]) == 1
        intent = payload["intents"][0]
        assert intent["status"] == "awaiting_approval"
        assert intent["requires_user_approval"] is True
        assert intent["live_submission_permitted"] is False
        assert intent["shares"] == 9
        assert intent["limit_price"] == 0.42

        blocked = execution_guard.stage_order_intent(
            ticker="TOO-RISKY",
            market_title="Too risky",
            side="YES",
            lane="primary",
            limit_price=0.60,
            shares=20,
            max_loss=12.00,
            path=path,
            now=NOW,
        )
        assert blocked["staged"] is False
        assert blocked["reason"] == "per_order_risk_limit"

        invalid = execution_guard.stage_order_intent(
            ticker="BAD-SIDE",
            market_title="Bad side",
            side="MAYBE",
            lane="primary",
            limit_price=0.40,
            shares=5,
            max_loss=2.00,
            path=path,
            now=NOW,
        )
        assert invalid["staged"] is False
        assert invalid["reason"] == "invalid_side"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "daily.json"
        # Five unique $10 intents consume the default $50 daily staging budget.
        for i in range(5):
            result = execution_guard.stage_order_intent(
                ticker=f"CAP-{i}",
                market_title=f"Cap {i}",
                side="NO",
                lane="primary",
                limit_price=0.50,
                shares=20,
                max_loss=10.00,
                path=path,
                now=AFTER_TEMP_DAILY_CAP_TEST,
            )
            assert result["staged"] is True

        cap = execution_guard.stage_order_intent(
            ticker="CAP-OVER",
            market_title="Cap over",
            side="NO",
            lane="primary",
            limit_price=0.50,
            shares=2,
            max_loss=1.00,
            path=path,
            now=AFTER_TEMP_DAILY_CAP_TEST,
        )
        assert cap["staged"] is False
        assert cap["reason"] == "daily_risk_limit"

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "temp-daily-cap.json"
        # During the temporary 24-hour window, the normal $50 staging cap is disabled.
        for i in range(6):
            result = execution_guard.stage_order_intent(
                ticker=f"TEMP-CAP-{i}",
                market_title=f"Temp cap {i}",
                side="YES",
                lane="primary",
                limit_price=0.50,
                shares=20,
                max_loss=10.00,
                path=path,
                now=NOW,
            )
            assert result["staged"] is True

    summary = execution_guard.queue_summary(Path("/definitely/not/a/real/queue.json"))
    assert summary["live_submission_permitted"] is False
    assert summary["mode"] in {"off", "preview"}

    print("Execution guard tests: PASS")


if __name__ == "__main__":
    main()
