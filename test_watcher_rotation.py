import unittest

from watcher import _priority_plus_rotation


class WatcherRotationTests(unittest.TestCase):
    def test_priority_is_reserved_and_tail_rotates(self):
        items = ["priority", "a", "b", "c", "d"]
        first, cursor = _priority_plus_rotation(
            items, limit=3, cursor=0, reserve_priority=1
        )
        second, cursor2 = _priority_plus_rotation(
            items, limit=3, cursor=cursor, reserve_priority=1
        )
        self.assertEqual(first, ["priority", "a", "b"])
        self.assertEqual(second, ["priority", "c", "d"])
        self.assertEqual(cursor2, 0)

    def test_rotation_wraps_without_duplicates_when_slots_fit(self):
        items = ["priority", "a", "b", "c"]
        selected, cursor = _priority_plus_rotation(
            items, limit=3, cursor=2, reserve_priority=1
        )
        self.assertEqual(selected, ["priority", "c", "a"])
        self.assertEqual(cursor, 1)

    def test_zero_limit_is_safe(self):
        selected, cursor = _priority_plus_rotation(
            ["a", "b"], limit=0, cursor=5, reserve_priority=1
        )
        self.assertEqual(selected, [])
        self.assertEqual(cursor, 0)


if __name__ == "__main__":
    unittest.main()
