from pathlib import Path
import json
import tempfile

import pushover_status


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_taken_confirmation_is_sent_once():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        queue = root / "queue.json"
        state = root / "state.json"
        _write(
            queue,
            {
                "version": 1,
                "intents": [
                    {
                        "intent_id": "abc123",
                        "market_title": "Milwaukee vs Pittsburgh: Milwaukee wins",
                        "ticker": "TEST-TICKER",
                        "side": "YES",
                        "shares": 9,
                        "limit_price": 0.38,
                        "status": "submitted",
                        "kalshi_order_id": "order-1",
                        "filled_shares": 0,
                    }
                ],
            },
        )
        sent = []

        def sender(title, message):
            sent.append((title, message))
            return True

        first = pushover_status.notify_taken_orders(
            queue_path=queue, state_path=state, sender=sender
        )
        second = pushover_status.notify_taken_orders(
            queue_path=queue, state_path=state, sender=sender
        )
        assert first["sent"] == 1
        assert second["sent"] == 0
        assert second["already_notified"] == 1
        assert len(sent) == 1
        assert sent[0][0] == "✅ TAKEN IN KALSHI"
        assert "Milwaukee vs Pittsburgh" in sent[0][1]
        assert "9 shares @ 38.0¢ max" in sent[0][1]


def test_unsubmitted_intent_does_not_notify():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        queue = root / "queue.json"
        state = root / "state.json"
        _write(
            queue,
            {
                "version": 1,
                "intents": [
                    {
                        "intent_id": "waiting",
                        "market_title": "Waiting bet",
                        "side": "NO",
                        "shares": 5,
                        "limit_price": 0.42,
                        "status": "awaiting_approval",
                        "kalshi_order_id": None,
                    }
                ],
            },
        )
        sent = []
        result = pushover_status.notify_taken_orders(
            queue_path=queue,
            state_path=state,
            sender=lambda title, message: sent.append((title, message)) or True,
        )
        assert result["checked"] == 0
        assert result["sent"] == 0
        assert sent == []


if __name__ == "__main__":
    test_taken_confirmation_is_sent_once()
    test_unsubmitted_intent_does_not_notify()
    print("Pushover taken-status tests: PASS")
