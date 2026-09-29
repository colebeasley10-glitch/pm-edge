import unittest
from unittest.mock import patch

from autotrader.paper_runner import _assert_no_kalshi_write_credentials


class PaperSecurityTests(unittest.TestCase):
    def test_startup_rejects_kalshi_write_credentials(self):
        with patch.dict("os.environ", {"KALSHI_WRITE_PRIVATE_KEY": "secret"}, clear=False):
            with self.assertRaises(RuntimeError):
                _assert_no_kalshi_write_credentials()

    def test_startup_allows_read_only_configuration(self):
        with patch.dict(
            "os.environ",
            {
                "KALSHI_WRITE_PRIVATE_KEY": "",
                "KALSHI_WRITE_API_KEY_ID": "",
                "KALSHI_API_KEY_ID": "",
                "KALSHI_PRIVATE_KEY_PATH": "",
            },
            clear=False,
        ):
            _assert_no_kalshi_write_credentials()


if __name__ == "__main__":
    unittest.main()
