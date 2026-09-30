import unittest
from autotrader.weather_shadow import parse_weather_contract, _matches, WeatherContract


class WeatherShadowTests(unittest.TestCase):
    def test_parse_high_bucket(self):
        m = {
            "ticker": "KXHIGHAUS-26SEP30-T97",
            "event_ticker": "KXHIGHAUS-26SEP30",
            "title": "Highest temperature in Austin today?",
            "yes_sub_title": "97° to 98°",
        }
        c = parse_weather_contract(m)
        self.assertIsNotNone(c)
        self.assertEqual(c.city, "Austin")
        self.assertEqual(c.target_date, "2026-09-30")
        self.assertEqual(c.variable, "temperature_2m_max")
        self.assertEqual((c.lower, c.upper), (97, 98))

    def test_parse_tail_bucket(self):
        m = {
            "ticker": "KXHIGHNY-26SEP30-T85",
            "event_ticker": "KXHIGHNY-26SEP30",
            "title": "Highest temperature in NYC today?",
            "yes_sub_title": "84° or below",
        }
        c = parse_weather_contract(m)
        self.assertIsNotNone(c)
        self.assertIsNone(c.lower)
        self.assertEqual(c.upper, 84)
        self.assertTrue(_matches(83.6, c))
        self.assertFalse(_matches(84.6, c))

    def test_unknown_bucket_fails_closed(self):
        m = {
            "ticker": "KXHIGHAUS-26SEP30-T97",
            "event_ticker": "KXHIGHAUS-26SEP30",
            "title": "Highest temperature in Austin today?",
            "yes_sub_title": "special settlement",
        }
        self.assertIsNone(parse_weather_contract(m))


if __name__ == "__main__":
    unittest.main()
