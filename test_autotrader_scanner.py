import unittest
from decimal import Decimal
from autotrader.scanner import discover

class ScannerTests(unittest.TestCase):
    def test_eligible_all_categories(self):
        markets = [
            {"ticker":"KXBTC", "title":"BTC", "status":"active", "volume_24h":"500",
             "yes_bid":".48", "yes_ask":".50", "yes_ask_size":"10",
             "no_bid":".49", "no_ask":".51", "no_ask_size":"10"},
            {"ticker":"KXNFL", "title":"NFL", "status":"active", "volume_24h":"500",
             "yes_bid":".48", "yes_ask":".50", "yes_ask_size":"10",
             "no_bid":".49", "no_ask":".51", "no_ask_size":"10"}]
        found=discover(markets)
        self.assertEqual(len(found),4)
        self.assertEqual({x.ticker for x in found},{"KXBTC","KXNFL"})

    def test_reject_illiquid_or_wide(self):
        markets=[{"ticker":"A", "status":"active", "volume_24h":"0",
                  "yes_bid":".20","yes_ask":".80","yes_ask_size":"10"},
                 {"ticker":"B", "status":"active", "volume_24h":"500",
                  "yes_bid":".20","yes_ask":".80","yes_ask_size":"10"}]
        self.assertEqual(discover(markets),[])

if __name__ == "__main__":
    unittest.main()
