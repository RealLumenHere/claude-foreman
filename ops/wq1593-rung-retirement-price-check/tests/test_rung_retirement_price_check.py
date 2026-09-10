#!/usr/bin/env python3
"""wq#1593 — tests for rung_retirement_price_check's pure decision functions.
Runs standalone, no yaml config or spend export needed:
python3 ops/wq1593-rung-retirement-price-check/tests/test_rung_retirement_price_check.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from rung_retirement_price_check import (
    model_names, removed_rungs, high_spend_retirements, price_points_disclosed,
    price_disclosures_by_rung, check_retirement, DEFAULT_THRESHOLD_USD,
)

OLD_LIST = [
    {"model_name": "mistral-small", "model_info": {"input_cost_per_token": 0.000000106}},
    {"model_name": "deepseek-v4-flash", "model_info": {"input_cost_per_token": 0.000000208}},
]
NEW_LIST = [
    {"model_name": "deepseek-v4-flash", "model_info": {"input_cost_per_token": 0.000000208}},
]
SPEND = {"mistral-small": 8.79, "deepseek-v4-flash": 45.40}
KNOWN = {"mistral-small", "deepseek-v4-flash", "gpt-oss-cheap"}

# Two high-spend rungs retiring onto one shared replacement — the multi-rung
# shape FLEET_COST_PLAN.md §6.6 plans for.
MULTI_OLD_LIST = OLD_LIST + [{"model_name": "gpt-oss-cheap"}]
MULTI_SPEND = dict(SPEND, **{"gpt-oss-cheap": 12.00})

GOOD_TICKET = (
    "Retiring mistral-small ($0.106/M measured input) and moving LPP onto "
    "deepseek-v4-flash ($0.208/M measured input)."
)
BAD_TICKET = "Retiring mistral-small, it's unused now."


class TestModelNames(unittest.TestCase):
    def test_extracts_names(self):
        self.assertEqual(model_names(OLD_LIST), {"mistral-small", "deepseek-v4-flash"})

    def test_empty_list(self):
        self.assertEqual(model_names([]), set())

    def test_none_list(self):
        self.assertEqual(model_names(None), set())

    def test_skips_entries_without_name(self):
        self.assertEqual(model_names([{"model_info": {}}]), set())


class TestRemovedRungs(unittest.TestCase):
    def test_detects_retired_rung(self):
        self.assertEqual(removed_rungs(OLD_LIST, NEW_LIST), {"mistral-small"})

    def test_no_change_no_removal(self):
        self.assertEqual(removed_rungs(OLD_LIST, OLD_LIST), set())

    def test_new_rung_added_not_removed(self):
        added = OLD_LIST + [{"model_name": "glm-5.3-flash"}]
        self.assertEqual(removed_rungs(OLD_LIST, added), set())

    def test_fully_retired_config(self):
        self.assertEqual(removed_rungs(OLD_LIST, []), {"mistral-small", "deepseek-v4-flash"})


class TestHighSpendRetirements(unittest.TestCase):
    def test_flags_rung_over_threshold(self):
        self.assertEqual(
            high_spend_retirements(OLD_LIST, NEW_LIST, SPEND, threshold_usd=5.0),
            ["mistral-small"],
        )

    def test_ignores_rung_under_threshold(self):
        low_spend = {"mistral-small": 0.50}
        self.assertEqual(
            high_spend_retirements(OLD_LIST, NEW_LIST, low_spend, threshold_usd=5.0), [],
        )

    def test_missing_spend_treated_as_zero(self):
        self.assertEqual(
            high_spend_retirements(OLD_LIST, NEW_LIST, {}, threshold_usd=5.0), [],
        )

    def test_default_threshold_matches_ticket_example(self):
        self.assertEqual(
            high_spend_retirements(OLD_LIST, NEW_LIST, SPEND, DEFAULT_THRESHOLD_USD),
            ["mistral-small"],
        )


class TestPriceDeltaDisclosure(unittest.TestCase):
    def test_counts_price_points(self):
        self.assertEqual(price_points_disclosed(GOOD_TICKET), 2)
        self.assertEqual(price_points_disclosed(BAD_TICKET), 0)
        self.assertEqual(price_points_disclosed(""), 0)

    def test_case_and_spacing_insensitive(self):
        self.assertEqual(price_points_disclosed("old: $0.106 / m   new: $0.208/M"), 2)

    def test_each_figure_attributed_to_its_own_rung(self):
        self.assertEqual(
            price_disclosures_by_rung(GOOD_TICKET, KNOWN),
            {"mistral-small": 1, "deepseek-v4-flash": 1, "gpt-oss-cheap": 0},
        )

    def test_unrelated_model_pricing_discloses_nothing(self):
        body = ("Retiring mistral-small, unused. Unrelated: gpt-4 was $5.00/M "
                "and claude-3 was $3.00/M back in the day.")
        self.assertEqual(price_points_disclosed(body), 2)
        self.assertEqual(price_disclosures_by_rung(body, KNOWN)["mistral-small"], 0)

    def test_nearest_rung_in_clause_claims_the_figure(self):
        body = "Retiring mistral-small ($0.106/M) and gpt-oss-cheap, onto deepseek-v4-flash ($0.208/M)"
        self.assertEqual(
            price_disclosures_by_rung(body, KNOWN),
            {"mistral-small": 1, "deepseek-v4-flash": 1, "gpt-oss-cheap": 0},
        )

    def test_name_matched_as_whole_token(self):
        body = "Retiring mistral-small-v2 ($0.106/M)."
        self.assertEqual(price_disclosures_by_rung(body, KNOWN)["mistral-small"], 0)

    def test_empty_body(self):
        self.assertEqual(price_disclosures_by_rung("", KNOWN), {r: 0 for r in KNOWN})


class TestCheckRetirement(unittest.TestCase):
    def test_passes_when_disclosed(self):
        self.assertEqual(
            check_retirement(OLD_LIST, NEW_LIST, SPEND, GOOD_TICKET, threshold_usd=5.0), [],
        )

    def test_refuses_when_undisclosed(self):
        violations = check_retirement(OLD_LIST, NEW_LIST, SPEND, BAD_TICKET, threshold_usd=5.0)
        self.assertEqual(len(violations), 2)
        self.assertIn("mistral-small", violations[0])
        self.assertIn("deepseek-v4-flash", violations[1])
        self.assertTrue(all(v.startswith("REFUSED") for v in violations))

    def test_passes_when_no_high_spend_retirement(self):
        self.assertEqual(
            check_retirement(OLD_LIST, NEW_LIST, {}, BAD_TICKET, threshold_usd=5.0), [],
        )

    def test_passes_when_nothing_retired(self):
        self.assertEqual(
            check_retirement(OLD_LIST, OLD_LIST, SPEND, BAD_TICKET, threshold_usd=5.0), [],
        )

    def test_refuses_when_replacement_price_missing(self):
        one_sided = "Retiring mistral-small ($0.106/M measured input), moving LPP off it."
        violations = check_retirement(OLD_LIST, NEW_LIST, SPEND, one_sided, threshold_usd=5.0)
        self.assertEqual(len(violations), 1)
        self.assertIn("deepseek-v4-flash", violations[0])

    def test_refuses_second_retirement_priced_only_by_the_first(self):
        """wq#1602 gap 1: two rungs retiring, two price figures — but one of
        them is the shared replacement's, so gpt-oss-cheap is undisclosed."""
        ticket = ("Retiring mistral-small ($0.106/M) and gpt-oss-cheap, moving "
                  "everything onto deepseek-v4-flash ($0.208/M).")
        violations = check_retirement(MULTI_OLD_LIST, NEW_LIST, MULTI_SPEND, ticket,
                                      threshold_usd=5.0)
        self.assertEqual(len(violations), 1)
        self.assertIn("gpt-oss-cheap", violations[0])

    def test_passes_when_every_retirement_priced(self):
        ticket = ("Retiring mistral-small ($0.106/M) and gpt-oss-cheap ($0.150/M), "
                  "moving everything onto deepseek-v4-flash ($0.208/M).")
        self.assertEqual(
            check_retirement(MULTI_OLD_LIST, NEW_LIST, MULTI_SPEND, ticket, threshold_usd=5.0), [],
        )

    def test_refuses_unrelated_pricing_padding(self):
        """wq#1602 gap 2: the D7 body padded with competitor pricing used to
        clear the gate on a bare body-wide count."""
        ticket = ("Retiring mistral-small, unused. Unrelated: gpt-4 was $5.00/M "
                  "and claude-3 was $3.00/M back in the day.")
        violations = check_retirement(OLD_LIST, NEW_LIST, SPEND, ticket, threshold_usd=5.0)
        self.assertEqual(len(violations), 2)
        self.assertIn("mistral-small", violations[0])

    def test_fully_retired_board_needs_no_replacement_price(self):
        body = "Retiring mistral-small ($0.106/M) and deepseek-v4-flash ($0.208/M); board is empty."
        self.assertEqual(check_retirement(OLD_LIST, [], SPEND, body, threshold_usd=5.0), [])

    def test_this_ticket_own_d7_example(self):
        """The exact wq#1593/D7 numbers: this is the ticket that should have
        been refused and wasn't."""
        violations = check_retirement(
            OLD_LIST, NEW_LIST, SPEND,
            "mistral-small is unused, removing it.",
            threshold_usd=5.0,
        )
        self.assertTrue(violations)


if __name__ == "__main__":
    unittest.main()
