import unittest
from decimal import Decimal
from autotrader.portfolio import PaperPortfolio

class PaperPortfolioTests(unittest.TestCase):
    def test_start_and_risk(self):
        p = PaperPortfolio()
        self.assertEqual(p.cash, Decimal("500"))
        self.assertFalse(p.buy("A", "YES", 30, ".50", ".50", 30))
        self.assertEqual(p.cash, Decimal("500"))

    def test_buy_sell_net_fees(self):
        p = PaperPortfolio()
        self.assertTrue(p.buy("A", "YES", 10, ".55", ".50", 10, ".01"))
        self.assertEqual(p.cash, Decimal("494.90"))
        self.assertFalse(p.buy("A", "NO", 1, ".50", ".50", 1))
        self.assertFalse(p.sell("A", ".70", 9))
        self.assertTrue(p.sell("A", ".70", 10, ".01"))
        self.assertEqual(p.cash, Decimal("501.80"))
        self.assertEqual(p.realized_pnl, Decimal("1.80"))

    def test_fee_inclusive_exposure_and_negative_proceeds_guard(self):
        p = PaperPortfolio()
        self.assertTrue(p.buy("A", "YES", 10, ".50", ".50", 10, ".01"))
        self.assertEqual(p.exposure(), Decimal("5.10"))
        self.assertEqual(p.snapshot(bids={"A": ".60"})["unrealized_pnl"], ".90")
        self.assertFalse(p.sell("A", ".005", 10, ".01"))
        self.assertEqual(p.cash, Decimal("494.90"))
        self.assertEqual(len(p.positions), 1)

    def test_price_guard_and_settlement(self):
        p = PaperPortfolio()
        self.assertFalse(p.buy("A", "YES", 5, ".40", ".41", 5))
        self.assertTrue(p.buy("A", "NO", 5, ".40", ".40", 5))
        self.assertTrue(p.settle("A", "YES"))
        self.assertEqual(p.realized_pnl, Decimal("-2.00"))
        self.assertEqual(p.cash, Decimal("498.00"))

if __name__ == "__main__":
    unittest.main()
