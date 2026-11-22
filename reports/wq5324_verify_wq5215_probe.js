'use strict';
// wq#5324 adversarial probes against src/stack-layout.js (pinned: ccc bc2e86b)
const path = process.argv[2];
const { computeStack } = require(path + '/src/stack-layout');
const tabs = (n) => Array.from({ length: n }, (_, i) => ({ id: i + 1 }));
const base = { x0: 200, top: 40, rowW: 1600, midH: 900, gap: 8, bottomN: 4, topFrac: 0.65 };
const out = {};
function overlaps(rs) {
  for (let i = 0; i < rs.length; i++) for (let j = i + 1; j < rs.length; j++) {
    const a = rs[i], b = rs[j];
    if (a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h) return [a.tab.id, b.tab.id];
  }
  return null;
}
function inBounds(rs, o) {
  return rs.every((r) => r.x >= o.x0 && r.x + r.w <= o.x0 + o.rowW && r.y >= o.top && r.y + r.h <= o.top + o.midH);
}
// 1. sweep: every tab count 1..12, every offset, odd widths: no overlap, in bounds, every rest tab reachable by paging
const sweep = [];
[1599, 1600, 1601, 333, 97].forEach((rowW) => {
  for (let n = 1; n <= 12; n++) {
    const row = tabs(n);
    for (let p = 0; p < n; p++) {
      const o = { ...base, rowW, row, primary: row[p] };
      const seen = new Set();
      const r0 = computeStack(o);
      for (let off = 0; off <= r0.maxOffset; off++) {
        const r = computeStack({ ...o, offset: off });
        const ov = overlaps(r.rects.filter((q) => q.w > 0));
        if (ov) sweep.push(['overlap', rowW, n, p, off, ov]);
        if (!inBounds(r.rects, o)) sweep.push(['oob', rowW, n, p, off]);
        if (r.rects[0].tab !== row[p]) sweep.push(['primary-moved', rowW, n, p, off]);
        if (r.rects.length > 1 + base.bottomN) sweep.push(['too-many', n]);
        r.bottom.forEach((t) => seen.add(t.id));
      }
      if (seen.size !== n - 1) sweep.push(['unreachable', rowW, n, p, seen.size]);
    }
  }
});
out.sweepFailures = sweep.slice(0, 10);
out.sweepFailureCount = sweep.length;
// 2. degenerate heights / widths
out.midHlessThanGap = computeStack({ ...base, row: tabs(3), midH: 4 }).rects.map((r) => r.h);
out.midH0 = computeStack({ ...base, row: tabs(3), midH: 0 }).rects.map((r) => r.h);
out.rowWlessThanGaps = computeStack({ ...base, row: tabs(5), rowW: 20 }).rects.map((r) => [r.x, r.w]);
// 3. config edge values
out.bottomN0 = computeStack({ ...base, row: tabs(8), bottomN: 0 }).bottom.length;
out.topFracNaN = computeStack({ ...base, row: tabs(3), topFrac: NaN }).rects[0].h;
out.topFrac2 = computeStack({ ...base, row: tabs(3), topFrac: 2 }).rects[0].h;
// 4. comment claim: "slots sized for bottomN even when fewer tiles remain"
out.threeTabsSmallWidths = computeStack({ ...base, row: tabs(3) }).rects.slice(1).map((r) => r.w);
out.fiveTabsSmallWidths = computeStack({ ...base, row: tabs(5) }).rects.slice(1).map((r) => r.w);
console.log(JSON.stringify(out, null, 1));
