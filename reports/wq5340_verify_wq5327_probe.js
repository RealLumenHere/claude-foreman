'use strict';
// wq#5340 adversarial sweep of wq#5327 (ccc 1128770): the real stripView() extracted from
// src/renderer/topbar.html vs computeStack() geometry for every (n, primary, offset, bottomN).
// Pin the input: run against a detached ccc checkout at 1128770, e.g.
//   git -C /home/lumen/ccc worktree add --detach /tmp/ccc-1128770 1128770
//   node reports/wq5340_verify_wq5327_probe.js /tmp/ccc-1128770
// Expect: {"cases":6006,"bad":0,...}. With the stack branch of stripView disabled the
// sweep reports bad > 0 (the probe can fail).
const fs = require('fs'), vm = require('vm'), path = require('path');
const root = process.argv[2];
const { computeStack } = require(path.join(root, 'src/stack-layout'));
const html = fs.readFileSync(path.join(root, 'src/renderer/topbar.html'), 'utf8');
const m = html.match(/\n  function stripView\(st, row\) \{[\s\S]*?\n  \}\n/);
const stripView = vm.runInNewContext('(' + m[0].trim() + ')');
let cases = 0, bad = [];
for (let n = 0; n <= 12; n++) {
  const row = Array.from({ length: n }, (_, i) => ({ id: 100 + i }));
  for (let bottomN = 1; bottomN <= 6; bottomN++) {
    for (let p = -1; p < n; p++) {
      for (let off = -1; off <= n + 1; off++) {
        const res = computeStack({ row, primary: p < 0 ? null : row[p], offset: off, bottomN, rowW: 1600, midH: 900, gap: 8 });
        const vis = res.rects.map((r) => r.tab.id);
        const v = stripView({ soloId: null, stackMode: true, pageOffset: off, columns: vis.length, maxOffset: res.maxOffset, visibleIds: vis }, row);
        const on = row.filter((t, i) => v.onscreen(t, i)).map((t) => t.id);
        const exp = row.filter((t) => vis.includes(t.id)).map((t) => t.id);
        cases++;
        if (JSON.stringify(on) !== JSON.stringify(exp)) bad.push({ n, bottomN, p, off, on, exp });
        if (n) {
          const big = row.indexOf(res.primary) + 1;
          const sp = res.bottom.map((t) => row.indexOf(t) + 1);
          const want = sp.length ? `${big} + ${Math.min(...sp)}–${Math.max(...sp)}/${n}` : `${big}/${n}`;
          if (v.rng !== want) bad.push({ n, bottomN, p, off, rng: v.rng, want });
        } else if (v.rng !== '–') bad.push({ n, rng: v.rng });
      }
    }
  }
}
// stale visibleIds (a laid-out tab closed before the next relayout): must not crash or light a stranger
const row = [{ id: 1 }, { id: 2 }];
const s1 = stripView({ soloId: null, stackMode: true, pageOffset: 0, columns: 3, visibleIds: [9, 1, 8] }, row);
const s2 = stripView({ soloId: null, stackMode: true, pageOffset: 0, columns: 0, visibleIds: [] }, row);
const s3 = stripView({ soloId: null, stackMode: true, pageOffset: 0, columns: 2, visibleIds: null }, row);
console.log(JSON.stringify({ cases, bad: bad.length, sample: bad.slice(0, 3),
  staleClosedPrimary: { on: row.filter((t, i) => s1.onscreen(t, i)).map((t) => t.id), rng: s1.rng },
  emptyVisible: s2.rng, nullVisibleFallback: { on: row.filter((t, i) => s3.onscreen(t, i)).map((t) => t.id), rng: s3.rng } }));
