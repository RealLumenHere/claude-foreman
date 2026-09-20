import inspect
import pathlib
import unittest

from pkg import client, config


class DefaultTest(unittest.TestCase):
    def test_config(self):
        self.assertEqual(config.DEFAULT_TIMEOUT_S, 45)

    def test_client_signature_default(self):
        self.assertEqual(inspect.signature(client.get).parameters["timeout"].default, 45)
        self.assertEqual(client.get_default("u")["timeout"], 45)

    def test_readme_agrees(self):
        text = pathlib.Path("README.md").read_text()
        self.assertIn("45 seconds", text)
        self.assertNotIn("30 seconds", text)
