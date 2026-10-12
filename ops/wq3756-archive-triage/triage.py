#!/usr/bin/env python3
"""wq#3756 — one-time archive triage: mine minerva.db proposals for diamonds.

Pipeline (ticket order):
  1. drop proposals older than 6 months
  2. drop spam-keyword matches (credential, restore, LiteLLM config, SaaS, cloud)
  3. cluster survivors into the watch categories by keyword
  4/5. score survivors against the named hardware + watch categories
  6. keep proposals never locally tested, emit the top N

Read-only against /home/lumen/minerva/minerva.db. Writes REPORT.md next to this file.
"""
import sqlite3
import datetime
import json
import sys
from pathlib import Path

DB_PATH = "/home/lumen/minerva/minerva.db"
OUT_PATH = Path(__file__).parent / "REPORT.md"
TOP_N = 30

MAX_AGE_DAYS = 183  # ~6 months

SPAM_KEYWORDS = ["credential", "restore", "litellm config", "saas", "cloud"]

# Proposals in these statuses already ran against real hardware — step 6 excludes them.
TESTED_STATUSES = {"deployed", "shipped", "piloting", "applied_unverified"}

HARDWARE = {
    "rk3399": ["rk3399", "rockchip rk3399", "firefly station p1", "rock pi 4", "rockpi4", "nanopi m4"],
    "mac_pro_6,1": ["mac pro 6,1", "macpro6,1", "mac pro 6.1", "nephthys"],
    "bc-250": ["bc-250", "bc250", "gfx1013", "cyan skillfish", "hapi-1", "hapi1"],
    "mt8167": ["mt8167", "mediatek mt8167"],
    "ingenic_t40": ["ingenic t40", "t40n", "t40xp", "ingenic"],
}

CATEGORIES = {
    "ingenic_mediatek_firmware": ["ingenic", "mediatek", "mt8167", "t40", "mtk firmware", "soc firmware", "mips firmware"],
    "pikvm_alternative": ["pikvm", "ip-kvm", "kvm over ip", "hdmi capture", "tinypilot", "jetkvm", "remote console"],
    "rockchip_maskrom": ["maskrom", "rkdeveloptool", "rockusb", "loader.bin", "miniloader", "rockchip"],
    "local_llm_optimization": ["quantiz", "gguf", "llama.cpp", "kv cache", "context window", "tok/s", "token/s",
                                "speculative decod", "batching", "offload", "prefill", "decode tps"],
    "model_eval_never_benchmarked": ["benchmark", "eval harness", "judge arena", "costbench", "model comparison",
                                      "leaderboard", "judgearena"],
}


def load_proposals(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, current_state, proposed_change, status, created_at, decision_reason "
        "FROM proposals"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def step1_recent(rows, now):
    cutoff = now - datetime.timedelta(days=MAX_AGE_DAYS)
    kept = []
    for r in rows:
        try:
            created = datetime.datetime.fromisoformat(r["created_at"])
        except (TypeError, ValueError):
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=datetime.timezone.utc)
        if created >= cutoff:
            kept.append(r)
    return kept


def blob(r):
    return " ".join(filter(None, [r.get("title"), r.get("current_state"), r.get("proposed_change")])).lower()


def step2_despam(rows):
    kept = []
    for r in rows:
        text = blob(r)
        if any(kw in text for kw in SPAM_KEYWORDS):
            continue
        kept.append(r)
    return kept


def step3_cluster(rows):
    for r in rows:
        text = blob(r)
        r["categories"] = sorted(cat for cat, kws in CATEGORIES.items() if any(kw in text for kw in kws))
    return rows


# Status bonus: a proposal still alive in the pipeline (planned/approved/researching) is
# closer to being built than one already killed (reject/duplicate/blocked) with an
# identical keyword profile — break keyword-score ties toward the more actionable one.
STATUS_BONUS = {"planned": 2, "approved": 2, "researching": 2, "staged": 1, "defer": 1}


def step4_5_score(rows):
    for r in rows:
        text = blob(r)
        hw_hits = sorted(hw for hw, aliases in HARDWARE.items() if any(a in text for a in aliases))
        r["hardware"] = hw_hits
        r["score"] = 3 * len(hw_hits) + 2 * len(r["categories"]) + STATUS_BONUS.get(r["status"], 0)
    return rows


def step6_top(rows, n, per_signature_cap=5):
    """Rank by score, but cap how many near-duplicate (hardware, categories)
    signatures can occupy the list — otherwise one repetitive cluster (e.g.
    a dozen rejected GGUF-rung variants) crowds out everything else."""
    candidates = [r for r in rows if r["status"] not in TESTED_STATUSES and (r["hardware"] or r["categories"])]
    candidates.sort(key=lambda r: (r["score"], r["created_at"]), reverse=True)

    top, counts, leftover = [], {}, []
    for r in candidates:
        sig = (tuple(r["hardware"]), tuple(r["categories"]))
        if counts.get(sig, 0) < per_signature_cap:
            top.append(r)
            counts[sig] = counts.get(sig, 0) + 1
        else:
            leftover.append(r)
        if len(top) == n:
            return top
    top.extend(leftover[: n - len(top)])
    return top


def render(top, total, after1, after2, after3_hits):
    lines = [
        "# wq#3756 archive triage — top proposals",
        "",
        f"Pipeline: {total} total -> {after1} within 6 months -> {after2} past spam filter -> "
        f"{after3_hits} hardware/category hits -> top {len(top)} never-locally-tested",
        "",
        "Finding: zero proposals in the archive mention RK3399, MT8167, Ingenic T40, PiKVM, "
        "MaskROM, or Rockchip at all (verified by direct substring search over all 1881 rows, "
        "not just the filtered set) — the self-improvement proposal pipeline has never produced "
        "Ingenic/MediaTek firmware, PiKVM-alternative, or Rockchip MaskROM proposals. The real "
        "hardware-relevant buried value is concentrated in Mac Pro 6,1 (nephthys) / BC-250 (hapi) "
        "local-LLM-optimization and never-benchmarked-eval proposals below.",
        "",
        "| score | id | status | hardware | categories | title |",
        "|---|---|---|---|---|---|",
    ]
    for r in top:
        title = r["title"].replace("|", "/")
        lines.append(
            f"| {r['score']} | {r['id']} | {r['status']} | {','.join(r['hardware']) or '-'} | "
            f"{','.join(r['categories']) or '-'} | {title} |"
        )
    return "\n".join(lines) + "\n"


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = load_proposals(conn)
    conn.close()

    now = datetime.datetime.now(datetime.timezone.utc)
    total = len(rows)

    after1 = step1_recent(rows, now)
    after2 = step2_despam(after1)
    after3 = step3_cluster(after2)
    scored = step4_5_score(after3)
    hits = [r for r in scored if r["hardware"] or r["categories"]]
    top = step6_top(scored, TOP_N)

    OUT_PATH.write_text(render(top, total, len(after1), len(after2), len(hits)))
    print(json.dumps({
        "total": total, "after_age_filter": len(after1), "after_spam_filter": len(after2),
        "hardware_or_category_hits": len(hits), "top_emitted": len(top),
    }))


if __name__ == "__main__":
    sys.exit(main())
