import unittest
from decimal import Decimal
from autotrader.engine import PaperEngine

MARKET = {"ticker":"DEMO", "title":"Demonstration market", "status":"active",
          "volume_24h":"500", "yes_bid":".48", "yes_ask":".50",
          "yes_ask_size":"20", "no_bid":".49", "no_ask":".51",
          "no_ask_size":"20"}
MARKET2 = {**MARKET, "ticker": "DEMO2", "title": "Second contract same event"}

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

    def test_dynamic_fee_used_for_both_gate_and_cash_debit(self):
        e = PaperEngine()
        start_cash = e.portfolio.cash
        result = e.on_snapshot(
            [MARKET],
            probabilities={("DEMO", "YES"): Decimal(".65")},
            quote_ages={"DEMO": 0},
            event_data_ages={"DEMO": 0},
        )
        self.assertEqual(result["paper_fills"], 1)
        decision = e.decisions[-1]
        contracts = Decimal(str(decision["contracts"]))
        fill = Decimal(str(decision["simulated_fill"]))
        fee_total = Decimal(str(decision["fee_total"]))
        self.assertEqual(start_cash - e.portfolio.cash, contracts * fill + fee_total)

    def test_stale_data_no_trade(self):
        e = PaperEngine()
        result = e.on_snapshot([MARKET], probabilities={("DEMO","YES"):Decimal(".90")},
                               quote_ages={"DEMO":5}, event_data_ages={"DEMO":0})
        self.assertEqual(result["paper_fills"], 0)

    def test_same_event_blocks_second_contract(self):
        e = PaperEngine()
        result = e.on_snapshot(
            [MARKET, MARKET2],
            probabilities={
                ("DEMO", "YES"): Decimal(".70"),
                ("DEMO2", "YES"): Decimal(".70"),
            },
            quote_ages={"DEMO": 0, "DEMO2": 0},
            event_data_ages={"DEMO": 0, "DEMO2": 0},
            contexts={
                ("DEMO", "YES"): {"event_id": "EVENT1", "fair_snapshot_id": "SNAP1"},
                ("DEMO2", "YES"): {"event_id": "EVENT1", "fair_snapshot_id": "SNAP2"},
            },
        )
        self.assertEqual(result["paper_fills"], 1)
        self.assertEqual(len(e.portfolio.positions), 1)

    def test_same_fair_snapshot_cannot_reenter_after_settlement(self):
        e = PaperEngine()
        args = dict(
            probabilities={("DEMO", "YES"): Decimal(".70")},
            quote_ages={"DEMO": 0},
            event_data_ages={"DEMO": 0},
            contexts={("DEMO", "YES"): {"event_id": "EVENT1", "fair_snapshot_id": "SNAP1"}},
        )
        self.assertEqual(e.on_snapshot([MARKET], **args)["paper_fills"], 1)
        self.assertTrue(e.portfolio.settle("DEMO", "YES"))
        self.assertEqual(e.on_snapshot([MARKET], **args)["paper_fills"], 0)

    def test_duplicate_snapshot_no_double_position(self):
        e = PaperEngine()
        args = dict(probabilities={("DEMO","YES"):Decimal(".65")},
                    quote_ages={"DEMO":0}, event_data_ages={"DEMO":0})
        self.assertEqual(e.on_snapshot([MARKET], **args)["paper_fills"], 1)
        self.assertEqual(e.on_snapshot([MARKET], **args)["paper_fills"], 0)
        self.assertEqual(len(e.portfolio.positions), 1)

if __name__ == "__main__":
    unittest.main()
