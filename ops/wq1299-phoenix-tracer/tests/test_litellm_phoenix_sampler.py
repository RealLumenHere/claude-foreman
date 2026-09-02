#!/usr/bin/env python3
"""wq#1299 — sampling-decision tests for litellm_phoenix_sampler.
Runs standalone, no litellm/opentelemetry install required:
python3 ops/wq1299-phoenix-tracer/tests/test_litellm_phoenix_sampler.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from litellm_phoenix_sampler import should_sample_success


class TestShouldSampleSuccess(unittest.TestCase):
    def test_rate_zero_never_samples(self):
        self.assertFalse(should_sample_success(0.0, rng=lambda: 0.0))

    def test_rate_one_always_samples(self):
        self.assertTrue(should_sample_success(1.0, rng=lambda: 0.999999))

    def test_negative_rate_never_samples(self):
        self.assertFalse(should_sample_success(-0.5, rng=lambda: 0.0))

    def test_boundary_below_threshold_samples(self):
        self.assertTrue(should_sample_success(0.2, rng=lambda: 0.1))

    def test_boundary_at_or_above_threshold_skips(self):
        self.assertFalse(should_sample_success(0.2, rng=lambda: 0.2))
        self.assertFalse(should_sample_success(0.2, rng=lambda: 0.5))

    def test_default_rng_is_bounded_probabilistic(self):
        # sanity check the real random.random default gives a plausible mix,
        # not a hardcoded constant, over many draws at rate=0.5
        hits = sum(should_sample_success(0.5) for _ in range(2000))
        self.assertTrue(700 < hits < 1300, f"unexpected sample count: {hits}")


if __name__ == "__main__":
    unittest.main()
