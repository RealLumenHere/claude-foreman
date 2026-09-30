#!/usr/bin/env python3
"""Scores both arms of pilot 45eab86a47df against sample.json's gold labels
and writes results.json. Primary metric: micro-F1 on entity-set match
(normalized-lowercase, set equality per item) across the fixed sample.
"""
import json
import os

import baseline_extract
import de_ner_skill

HERE = os.path.dirname(os.path.abspath(__file__))


def score(get_pred, items):
    tp = fp = fn = 0
    per_item = []
    for item in items:
        gold = set(item["gold_entities"])
        pred = get_pred(item)
        item_tp = len(gold & pred)
        item_fp = len(pred - gold)
        item_fn = len(gold - pred)
        tp += item_tp
        fp += item_fp
        fn += item_fn
        per_item.append({"id": item["id"], "gold": sorted(gold),
                          "pred": sorted(pred), "tp": item_tp,
                          "fp": item_fp, "fn": item_fn})
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0.0)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 4),
            "recall": round(recall, 4), "f1": round(f1, 4),
            "per_item": per_item}


def main():
    sample = json.load(open(os.path.join(HERE, "sample.json")))
    items = sample["items"]

    baseline_result = score(
        lambda it: baseline_extract.extract(it["title"] + " " + it["reasoning"]),
        items)
    treatment_result = score(lambda it: de_ner_skill.run(it["id"]), items)

    out = {
        "sample_n": len(items),
        "gold_entities_total": sum(len(it["gold_entities"]) for it in items),
        "baseline": baseline_result,
        "treatment": treatment_result,
    }
    with open(os.path.join(HERE, "results.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps({"baseline_f1": baseline_result["f1"],
                       "treatment_f1": treatment_result["f1"],
                       "baseline_recall": baseline_result["recall"],
                       "treatment_recall": treatment_result["recall"],
                       "baseline_precision": baseline_result["precision"],
                       "treatment_precision": treatment_result["precision"]},
                      indent=2))


if __name__ == "__main__":
    main()
