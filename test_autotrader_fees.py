import unittest
from decimal import Decimal

from autotrader.fees import prediction_fee


class FeeTests(unittest.TestCase):
    def test_general_taker_fee_rounds_up_at_order_level(self):
        self.assertEqual(prediction_fee(1, "0.50"), Decimal("0.02"))
        self.assertEqual(prediction_fee(20, "0.50"), Decimal("0.35"))

    def test_zero_multiplier_means_zero_fee(self):
        self.assertEqual(
            prediction_fee(25, "0.50", rate="0.0175", multiplier="0"),
            Decimal("0"),
        )

    def test_fee_rejects_invalid_price(self):
        with self.assertRaises(ValueError):
            prediction_fee(1, "1.00")


if __name__ == "__main__":
    unittest.main()
