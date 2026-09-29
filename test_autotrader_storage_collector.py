import json
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
from autotrader.portfolio import PaperPortfolio
from autotrader.storage import save_portfolio, load_portfolio
from autotrader.collector import collect_once


class FakeClient:
    def list_open_markets(self, max_markets=3000):
        return [{"ticker":"KXTEST", "title":"Test", "status":"active",
                 "volume_24h_fp":"500", "yes_bid_dollars":"0.48",
                 "yes_ask_dollars":"0.50", "no_bid_dollars":"0.49",
                 "no_ask_dollars":"0.51", "yes_ask_size_fp":"20"}]


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

    def test_collector_observes_but_never_trades(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            observations = Path(directory) / "observations.jsonl"
            result = collect_once(FakeClient(), state_path=state, observations_path=observations)
            self.assertEqual(result["paper_trades"], 0)
            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["portfolio"]["cash"], "500.00")
            self.assertFalse(state.exists())
            self.assertEqual(len(observations.read_text().splitlines()), 1)
            self.assertEqual(json.loads(observations.read_text())["candidates"][0]["ticker"], "KXTEST")

if __name__ == "__main__":
    unittest.main()
