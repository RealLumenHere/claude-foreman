import inspect
import unittest

from pkg import pipeline


class PipelineTest(unittest.TestCase):
    def test_default_unchanged(self):
        self.assertEqual(pipeline.run([" a ", "b"]), ["a", "b"])

    def test_prefix_threaded(self):
        self.assertEqual(pipeline.run([" a "], prefix=">> "), [">> a"])
        self.assertEqual(pipeline.emit("x", prefix="#"), "#x")

    def test_prefix_is_keyword_only(self):
        p = inspect.signature(pipeline.run).parameters["prefix"]
        self.assertEqual(p.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(p.default, "")
