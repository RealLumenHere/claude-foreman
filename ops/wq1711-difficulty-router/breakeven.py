"""wq#1711: break-even escalation rate per workload shape.

A post-hoc cascade pays the cheap rung on every request and the expensive rung
only on the escalated fraction e. Against always-calling the expensive rung:

    saving(f, e) = f * [ (c_hi - c_lo) - e * c_hi ]

so for any routed fraction f, the cascade breaks even at

    e* = 1 - c_lo / c_hi

which is independent of f. Rates are the measured $/Mtok fits from
research/FLEET_COST_PLAN.md §3 (glm is Z.ai list, qwen36-local is free).

Run: python3 ops/wq1711-difficulty-router/breakeven.py
"""

RATES = {  # $/M tokens: (input, output)
    "qwen36-local": (0.0, 0.0),
    "glm-5.3-flash-zai": (0.15, 0.50),
    "cerebras-oss-120b": (0.200, 0.793),
    "deepseek-v4-flash": (0.208, 0.251),
    "deepseek-v4-pro": (0.432, 1.232),
}

SHAPES = {  # avg prompt / completion tokens per request
    "LPP turn (measured on glm, 8.3)": (4_635, 432),
    "ops-emergency recurring": (2_236, 1_162),
    "minerva classifier": (3_485, 152),
    "lumen-agent (output-heavy)": (1_743, 1_984),
}

PAIRS = [
    ("qwen36-local", "glm-5.3-flash-zai"),
    ("qwen36-local", "deepseek-v4-flash"),
    ("glm-5.3-flash-zai", "deepseek-v4-pro"),
    ("deepseek-v4-flash", "deepseek-v4-pro"),
]


def cost(model, shape):
    rin, rout = RATES[model]
    prompt, completion = shape
    return (prompt * rin + completion * rout) / 1e6


def main():
    print(f"{'shape':34} {'cheap -> expensive':44} {'$cheap':>9} {'$exp':>9} {'e*':>7}")
    for shape_name, shape in SHAPES.items():
        for lo, hi in PAIRS:
            c_lo, c_hi = cost(lo, shape), cost(hi, shape)
            if c_hi <= 0:
                continue
            e_star = 1 - c_lo / c_hi
            print(f"{shape_name:34} {lo + ' -> ' + hi:44} {c_lo:9.6f} {c_hi:9.6f} {e_star:6.0%}")


if __name__ == "__main__":
    main()
