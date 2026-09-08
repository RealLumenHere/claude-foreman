#!/usr/bin/env python3
"""wq#1504 — apk datasource customManager regex must match the documented
annotation pattern (README.md's own example), not just parse as valid JS.
Runs standalone: python3 ops/tests/test_renovate_apk_datasource.py
Shells out to `node` to load config.js as CommonJS and exercise the real
regex the running Renovate process uses; nothing live is touched."""
import json
import os
import subprocess
import sys
import unittest

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, "wq859-renovate-gitea", "config.js")

NODE_SCRIPT = """
const cfg = require(process.argv[1]);
const mgr = cfg.customManagers.find(m => m.datasourceTemplate === 'apk');
if (!mgr) { console.log(JSON.stringify({found: false})); process.exit(0); }
const re = new RegExp(mgr.matchStrings[0]);
const sample = process.argv[2];
const m = sample.match(re);
console.log(JSON.stringify({
  found: true,
  fileMatch: mgr.fileMatch,
  matched: !!m,
  groups: m ? m.groups : null,
}));
"""

# The exact example from ops/wq859-renovate-gitea/README.md's "Alpine apk
# package tracking" section.
SAMPLE = (
    '# renovate: datasource=apk depName=curl versioning=loose\n'
    'ENV CURL_VERSION="8.9.1-r1"\n'
    'RUN apk add --no-cache curl=$CURL_VERSION\n'
)


def run_node(sample):
    out = subprocess.run(
        ["node", "-e", NODE_SCRIPT, "--", CONFIG, sample],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


class ApkDatasourceTest(unittest.TestCase):
    def test_apk_custom_manager_is_registered(self):
        result = run_node(SAMPLE)
        self.assertTrue(result["found"], "no customManager with datasourceTemplate=apk")

    def test_dockerfile_filematch_covers_plain_and_suffixed_dockerfiles(self):
        result = run_node(SAMPLE)
        combined = "\n".join(result["fileMatch"])
        self.assertRegex(combined, r"Dockerfile")

    def test_regex_extracts_depname_versioning_and_currentvalue_from_readme_example(self):
        result = run_node(SAMPLE)
        self.assertTrue(result["matched"], "regex did not match README's documented example")
        self.assertEqual(result["groups"]["depName"], "curl")
        self.assertEqual(result["groups"]["versioning"], "loose")
        self.assertEqual(result["groups"]["currentValue"], "8.9.1-r1")

    def test_regex_does_not_match_an_unannotated_env_line(self):
        result = run_node('ENV CURL_VERSION="8.9.1-r1"\n')
        self.assertFalse(result["matched"])


if __name__ == "__main__":
    unittest.main()
