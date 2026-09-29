import json
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
from autotrader.portfolio import PaperPortfolio
from autotrader.storage import save_portfolio, load_portfolio
from autotrader.collector import collect_once


class FakeClient:
    def __init__(self):
        self.orderbook_calls = []

    def list_open_markets(self, max_markets=3000):
        return [{
            "ticker": "KXTEST",
            "title": "Test",
            "status": "active",
            "volume_24h_fp": "500",
            "yes_bid_dollars": "0.48",
            "yes_ask_dollars": "0.51",
            "no_bid_dollars": "0.49",
            "no_ask_dollars": "0.52",
        }]

    def get_orderbook(self, ticker, depth=20):
        self.orderbook_calls.append((ticker, depth))
        return {
            "orderbook_fp": {
                "yes_dollars": [["0.40", "100"], ["0.48", "20"]],
                "no_dollars": [["0.41", "100"], ["0.49", "30"]],
            }
        }


class StorageCollectorTests(unittest.TestCase):
    def test_round_trip_and_duplicate_prevention(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.json"
            p = PaperPortfolio()
            self.assertTrue(p.buy("A", "YES", 10, ".5", ".5", 10, ".01"))
            save_portfolio(p, path)
            restored = load_portfolio(path)
            self.assertEqual(restored.cash, Decimal("494.90"))
            self.assertEqual(restored.exposure(), Decimal("5.10"))
            self.assertFalse(restored.buy("A", "NO", 1, ".5", ".5", 1))
            self.assertEqual(len(restored.ledger), 1)

    def test_collector_observes_real_orderbook_depth_but_never_trades(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            observations = Path(directory) / "observations.jsonl"
            client = FakeClient()
            result = collect_once(
                client, state_path=state, observations_path=observations
            )
            self.assertEqual(result["paper_trades"], 0)
            self.assertEqual(result["candidate_count"], 2)
            self.assertEqual(result["portfolio"]["cash"], "500.00")
            self.assertFalse(state.exists())
            self.assertEqual(client.orderbook_calls, [("KXTEST", 1)])
            rows = json.loads(observations.read_text())
            candidates = {(c["side"], c["ask"], c["depth"]) for c in rows["candidates"]}
            self.assertIn(("YES", "0.51", "30"), candidates)
            self.assertIn(("NO", "0.52", "20"), candidates)

    def test_orderbook_failure_fails_closed(self):
        class BrokenBook(FakeClient):
            def get_orderbook(self, ticker, depth=20):
                raise RuntimeError("unavailable")

        with tempfile.TemporaryDirectory() as directory:
            result = collect_once(
                BrokenBook(),
                state_path=Path(directory) / "state.json",
                observations_path=Path(directory) / "obs.jsonl",
            )
            self.assertEqual(result["candidate_count"], 0)
            self.assertEqual(result["paper_trades"], 0)


if __name__ == "__main__":
    unittest.main()
