"""wq#5298 adversarial probe of wq#5177's contrast clip-box skip (rules.js insideAll)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_renderlint import tree, lint  # noqa: E402

# #ell: a faint (#bbb on #fff, ~1.9:1) label that is ellipsized INSIDE a span, so its text node's
# parent is the span and the ellipsis box is a clipping ancestor. The line is visible on screen
# (the ellipsized head of it is painted), but its range rect runs past the clip box's right edge.
# #short: same colours, fits -> control that the rule is live.
ELLIPSIS = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;font:14px/20px sans-serif;background:#fff;color:#111}
.row{width:200px;margin:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;background:#fff}
.faint{color:#bbb}</style></head><body>
<div class="row"><span id="ell" class="faint">A very long faint chat name that is ellipsized at the edge</span></div>
<div class="row"><span id="short" class="faint">Short faint</span></div>
</body></html>"""


def test_probe_ellipsized_faint_text_is_still_contrast_checked(tmp_path):
    f = [x for x in lint(tree(tmp_path, ELLIPSIS), widths=(360,)) if x["rule"] == "contrast"]
    sels = {x["selector"] for x in f}
    assert "#short" in sels, f      # control: the rule is live
    assert "#ell" in sels, f        # the visible, ellipsized faint label must not be skipped


# A horizontally scrolling chip bar: #cut is half off the right edge (reachable by scrolling, so
# text-clipped stays quiet by design); its visible head is painted faint on white.
CHIPS = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;font:14px/20px sans-serif;background:#fff;color:#111}
.bar{width:300px;overflow-x:auto;white-space:nowrap;background:#fff;padding:4px}
.chip{display:inline-block;margin-right:8px;color:#bbb}</style></head><body>
<div class="bar" style="width:150px"><span id="whole" class="chip">Visible</span><span id="cut" class="chip">Faint chip off edge</span></div>
</body></html>"""


def test_probe_horizontally_scrolled_partial_chip(tmp_path):
    f = [x for x in lint(tree(tmp_path, CHIPS), widths=(360,)) if x["severity"] == "error"]
    print(sorted({(x["rule"], x["selector"]) for x in f}))


def test_probe_ellipsized_text_has_no_other_rule_either(tmp_path):
    f = [x for x in lint(tree(tmp_path, ELLIPSIS), widths=(360,)) if x["severity"] == "error"]
    print(sorted({(x["rule"], x["selector"]) for x in f}))
