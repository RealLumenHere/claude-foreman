import pathlib
import unittest

from pkg import report, store


class RenameTest(unittest.TestCase):
    def test_new_name_works(self):
        db = {"a": 1}
        self.assertEqual(store.load_row(db, "a"), 1)
        self.assertEqual(store.fetch_many(db, ["a", "z"]), [1, None])
        self.assertEqual(report.describe(db, "a"), "a=1")

    def test_old_name_gone_everywhere(self):
        for p in pathlib.Path("pkg").glob("*.py"):
            self.assertNotIn("fetch_row", p.read_text(), p.name)
