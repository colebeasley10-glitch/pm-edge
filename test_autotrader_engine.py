import unittest
from decimal import Decimal
from autotrader.engine import PaperEngine

MARKET = {"ticker":"DEMO", "title":"Demonstration market", "status":"active",
          "volume_24h":"500", "yes_bid":".48", "yes_ask":".50",
          "yes_ask_size":"20", "no_bid":".49", "no_ask":".51",
          "no_ask_size":"20"}

class EngineTests(unittest.TestCase):
    def test_no_probability_no_trade(self):
        e = PaperEngine()
        result = e.on_snapshot([MARKET], probabilities={}, quote_ages={"DEMO":0},
                               event_data_ages={"DEMO":0})
        self.assertEqual(result["paper_fills"], 0)
        self.assertEqual(e.portfolio.cash, Decimal("500"))

    def test_fresh_independent_edge_paper_fill(self):
        e = PaperEngine()
        result = e.on_snapshot([MARKET], probabilities={("DEMO","YES"):Decimal(".65")},
                               quote_ages={"DEMO":0}, event_data_ages={"DEMO":0},
                               fee_per_contract=".01")
        self.assertEqual(result["paper_fills"], 1)
        self.assertEqual(e.portfolio.positions["DEMO"].side, "YES")
        self.assertLessEqual(Decimal("500")-e.portfolio.cash, Decimal("10"))

    def test_stale_data_no_trade(self):
        e = PaperEngine()
        result = e.on_snapshot([MARKET], probabilities={("DEMO","YES"):Decimal(".90")},
                               quote_ages={"DEMO":5}, event_data_ages={"DEMO":0})
        self.assertEqual(result["paper_fills"], 0)

    def test_duplicate_snapshot_no_double_position(self):
        e = PaperEngine()
        args = dict(probabilities={("DEMO","YES"):Decimal(".65")},
                    quote_ages={"DEMO":0}, event_data_ages={"DEMO":0})
        self.assertEqual(e.on_snapshot([MARKET], **args)["paper_fills"], 1)
        self.assertEqual(e.on_snapshot([MARKET], **args)["paper_fills"], 0)
        self.assertEqual(len(e.portfolio.positions), 1)

if __name__ == "__main__":
    unittest.main()
