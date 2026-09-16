#!/usr/bin/env python3
"""build_sample.py — pilot 78590aee9360 (wq#1903).

The proposal names "the arXiv benchmark's questions" but neither the proposal
text nor minerva.db's proposed_change/decision_reason for this id names a
paper id or dataset URL (verified: `grep -i logical pilots/78590aee9360/*`
finds only the CARD's own two mentions). There is nothing pinnable to
download, so this pilot does not guess an arXiv id. Instead it authors a
small fixed sample of the same item *type* the proposal describes — classic
propositional-logic validity questions with an unambiguous True/False answer
key — and discloses the substitution in CARD.md and REPORT.md. If a real
benchmark id turns up later, swap this file's ITEMS for the real set; the
runner (eval_logical_inference.py) does not care where the sample came from.

Deterministic and hand-authored, so no draw from minerva.db and no
credentials touch this file, satisfying the Data handling note in CARD.md
(nothing here needs a fleet extract).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Each item is a single valid/invalid propositional-logic inference.
# `answer` is "True" if the conclusion follows necessarily from the premises,
# "False" otherwise (including classic invalid forms like affirming the
# consequent). `pattern` documents which inference form is being tested.
ITEMS = [
    {"id": 1, "pattern": "modus_ponens", "prompt": "Premises: If it rains, the ground is wet. It is raining. Conclusion: The ground is wet. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 2, "pattern": "modus_tollens", "prompt": "Premises: If the alarm is armed, the light blinks. The light is not blinking. Conclusion: The alarm is not armed. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 3, "pattern": "affirming_consequent", "prompt": "Premises: If it rains, the ground is wet. The ground is wet. Conclusion: It is raining. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "False"},
    {"id": 4, "pattern": "denying_antecedent", "prompt": "Premises: If it rains, the ground is wet. It is not raining. Conclusion: The ground is not wet. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "False"},
    {"id": 5, "pattern": "disjunctive_syllogism", "prompt": "Premises: Either the switch is on or the switch is off. The switch is not on. Conclusion: The switch is off. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 6, "pattern": "hypothetical_syllogism", "prompt": "Premises: If A then B. If B then C. A is true. Conclusion: C is true. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 7, "pattern": "invalid_disjunct", "prompt": "Premises: Either the switch is on or the switch is off. The switch is on. Conclusion: The switch is off. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "False"},
    {"id": 8, "pattern": "syllogism_valid", "prompt": "Premises: All cats are mammals. All mammals are animals. Conclusion: All cats are animals. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 9, "pattern": "syllogism_invalid_undistributed", "prompt": "Premises: All cats are mammals. Some mammals can swim. Conclusion: Some cats can swim. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "False"},
    {"id": 10, "pattern": "existential_from_universal", "prompt": "Premises: All unicorns are white. Conclusion: Some unicorns are white. (Assume no unicorns exist.) Is the conclusion necessarily true given the premises alone, without assuming unicorns exist? Answer True or False.", "answer": "False"},
    {"id": 11, "pattern": "contrapositive", "prompt": "Premises: If a number is divisible by 4, it is divisible by 2. 7 is not divisible by 2. Conclusion: 7 is not divisible by 4. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 12, "pattern": "de_morgan_and", "prompt": "Premises: It is not the case that (it is raining and it is cold). It is raining. Conclusion: It is not cold. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 13, "pattern": "de_morgan_or_invalid", "prompt": "Premises: It is not the case that (it is raining or it is cold). Conclusion: It is raining. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "False"},
    {"id": 14, "pattern": "transitivity", "prompt": "Premises: Alice is taller than Bob. Bob is taller than Carl. Conclusion: Alice is taller than Carl. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 15, "pattern": "false_transitivity", "prompt": "Premises: Alice knows Bob. Bob knows Carl. Conclusion: Alice knows Carl. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "False"},
    {"id": 16, "pattern": "biconditional", "prompt": "Premises: The door is locked if and only if the key is turned. The key is turned. Conclusion: The door is locked. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 17, "pattern": "biconditional_negative", "prompt": "Premises: The door is locked if and only if the key is turned. The key is not turned. Conclusion: The door is locked. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "False"},
    {"id": 18, "pattern": "quantifier_scope", "prompt": "Premises: Every student read some book. Conclusion: There is some book that every student read. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "False"},
    {"id": 19, "pattern": "conjunction_elimination", "prompt": "Premises: It is sunny and it is warm. Conclusion: It is warm. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "True"},
    {"id": 20, "pattern": "disjunction_introduction_invalid", "prompt": "Premises: It is sunny. Conclusion: It is sunny or it is snowing, and it is not sunny. Is the conclusion necessarily true given the premises? Answer True or False.", "answer": "False"},
]


def main() -> None:
    out_dir = Path(__file__).parent
    sample = {"items": ITEMS}
    payload = json.dumps(sample, indent=2, sort_keys=True)
    out_path = out_dir / "sample.json"
    out_path.write_text(payload + "\n")
    digest = hashlib.sha256((payload + "\n").encode()).hexdigest()
    print(f"wrote {out_path} ({len(ITEMS)} items) sha256={digest}")


if __name__ == "__main__":
    main()
