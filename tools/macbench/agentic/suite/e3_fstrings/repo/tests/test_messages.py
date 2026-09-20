import pathlib
import re
import unittest

from pkg import messages


class MessagesTest(unittest.TestCase):
    def test_behaviour_unchanged(self):
        self.assertEqual(messages.greet("bo"), "hello, bo")
        self.assertEqual(messages.count(3, "rows"), "3 rows found")
        self.assertEqual(messages.pair(1, "x"), "(1, x)")
        self.assertEqual(messages.ratio(0.5, 7), "0.50 of 7")
        self.assertEqual(messages.keyed({"k": "a", "v": 2}), "a=2")

    def test_no_percent_formatting_left(self):
        src = pathlib.Path("pkg/messages.py").read_text()
        self.assertIsNone(re.search(r"%[sd(]|%\.\d", src))
        self.assertGreaterEqual(src.count('f"'), 5)
