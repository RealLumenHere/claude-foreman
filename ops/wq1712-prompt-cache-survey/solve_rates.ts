// wq#1712 — recover the gateway's registered DeepSeek rates from live billing.
//
// cost_header_probe.ts showed `x-litellm-response-cost` differs between a 94%-hit
// and a 98%-hit call at identical prompt_tokens, so the gateway DOES price cache
// reads separately. The rate itself is what the $/mo estimate rests on, and
// `/model/info` is admin-scoped (401 from this key — see README). But the rates
// are recoverable: each call gives one equation
//
//     cost = h*cache_hit_tokens + m*cache_miss_tokens + o*completion_tokens
//
// so N calls with different (hit, miss, output) mixes over-determine the three
// unknowns. This fires four deliberately dissimilar shapes and least-squares
// solves for (h, m, o) — giving the cache-hit price as the gateway actually bills
// it, not as any doc claims.
//
// Shapes, chosen so the design matrix is well conditioned:
//   1. long + fresh nonce   -> mostly miss
//   2. long, repeated       -> mostly hit
//   3. short + fresh nonce  -> small, mostly miss
//   4. long, repeated, with a prompt that provokes a longer completion
//
// Run via lumen-run (needs the LPP-scoped gateway key):
//   /home/lumen/bin/lumen-run start --name wq1712-rates -- \
//     /home/lumen/pokemon/node_modules/.bin/tsx \
//     /home/lumen/worktrees/wq1712-a1/ops/wq1712-prompt-cache-survey/solve_rates.ts

import { writeFileSync, readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { History, buildMessages, renderState } from "/home/lumen/pokemon/src/prompt.ts";
import { CFG } from "/home/lumen/pokemon/src/config.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const MODEL = process.env.WQ1712_MODEL ?? "deepseek-v4-flash";

function apiKey(): string {
  if (process.env.LITELLM_MASTER_KEY) return process.env.LITELLM_MASTER_KEY;
  return readFileSync("/home/lumen/.secrets/litellm-service-keys/lpp.key", "utf8").trim();
}

const stateForRender = {
  map: 12, x: 6, y: 9, badgeCount: 1, money: 1340, inBattle: 0,
  party: [{ species: 1, level: 14, hp: 38, maxHp: 42, status: 0, moves: [1, 45, 33, 0], pp: [30, 20, 20, null] }],
} as any;
const mapAscii = Array.from({ length: 9 }, (_, r) =>
  Array.from({ length: 9 }, (_, c) => (r === 4 && c === 4 ? "P" : (r + c) % 5 === 0 ? "G" : (r + c) % 7 === 0 ? "#" : ".")).join("")
).join("\n");
const acts = ["goto(6,9)", "interact(5,8)", "talk()", "leave_edge(N)", "goto(4,3)"];

function promptOf(turns: number, notepad: string) {
  const hist = new History();
  for (let t = 1; t <= turns; t++) {
    hist.push(`T${t} [map${10 + (t % 3)} ${(t * 3) % 20},${(t * 2) % 18}] ${acts[t % acts.length]} => moved to (${(t * 3) % 20},${(t * 2) % 18}) on map${10 + (t % 3)}, no new events, party healthy`.slice(0, 190));
  }
  return buildMessages(hist, notepad, renderState(stateForRender, mapAscii) + "\nSCREEN TEXT: (none)", "", "");
}

type Row = {
  tag: string; status: number; cost: number | null;
  hit: number; miss: number; out: number; error: unknown;
};

async function call(tag: string, messages: unknown, maxTok: number): Promise<Row> {
  const r = await fetch(CFG.gateway, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${apiKey()}` },
    body: JSON.stringify({ model: MODEL, messages, max_tokens: maxTok, temperature: 0.6 }),
  });
  const d: any = await r.json().catch(() => ({}));
  const u = d.usage ?? {};
  const costHeader = r.headers.get("x-litellm-response-cost");
  // DeepSeek names the split `prompt_cache_{hit,miss}_tokens`; every other rung
  // reports only the OpenAI-shaped `prompt_tokens_details.cached_tokens`. Without
  // the fallback both columns are constant zero on those rungs and the design
  // matrix is singular.
  const hit = u.prompt_cache_hit_tokens ?? u.prompt_tokens_details?.cached_tokens ?? 0;
  const prompt = u.prompt_tokens ?? 0;
  return {
    tag, status: r.status,
    cost: costHeader === null ? null : Number(costHeader),
    hit,
    miss: u.prompt_cache_miss_tokens ?? Math.max(0, prompt - hit),
    out: u.completion_tokens ?? 0,
    error: d.error ?? null,
  };
}

/** Least-squares solve of A x = b via the 3x3 normal equations, Gaussian
 *  elimination with partial pivoting. Three unknowns, no dependency needed. */
function leastSquares3(A: number[][], b: number[]): number[] {
  const N = [[0, 0, 0], [0, 0, 0], [0, 0, 0]].map(r => r.slice());
  const c = [0, 0, 0];
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) N[i][j] = A.reduce((s, r) => s + r[i] * r[j], 0);
    c[i] = A.reduce((s, r, k) => s + r[i] * b[k], 0);
  }
  for (let col = 0; col < 3; col++) {
    let piv = col;
    for (let r = col + 1; r < 3; r++) if (Math.abs(N[r][col]) > Math.abs(N[piv][col])) piv = r;
    [N[col], N[piv]] = [N[piv], N[col]];
    [c[col], c[piv]] = [c[piv], c[col]];
    if (N[col][col] === 0) throw new Error("singular design matrix — shapes were too similar");
    for (let r = col + 1; r < 3; r++) {
      const f = N[r][col] / N[col][col];
      for (let j = col; j < 3; j++) N[r][j] -= f * N[col][j];
      c[r] -= f * c[col];
    }
  }
  const x = [0, 0, 0];
  for (let i = 2; i >= 0; i--) {
    let s = c[i];
    for (let j = i + 1; j < 3; j++) s -= N[i][j] * x[j];
    x[i] = s / N[i][i];
  }
  return x;
}

const sleep = (ms: number) => new Promise(res => setTimeout(res, ms));

(async () => {
  const nonce = String(Date.now());
  const long = promptOf(175, `PIN: rates ${nonce}\nExploring Route 3, low on poke_balls\nNext: Pewter Mart`);
  const short = promptOf(8, `PIN: short ${nonce}\nJust left Viridian`);

  const rows: Row[] = [];
  rows.push(await call("long_fresh", long, 3200));
  await sleep(3000);
  rows.push(await call("long_repeat", long, 3200));
  await sleep(3000);
  rows.push(await call("short_fresh", short, 3200));
  await sleep(3000);
  rows.push(await call("long_repeat_2", long, 3200));

  const usable = rows.filter(r => r.status === 200 && r.cost !== null && r.cost > 0);
  const out: any = { ts: new Date().toISOString(), model: MODEL, rows };

  if (usable.length >= 3) {
    try {
    const [h, m, o] = leastSquares3(usable.map(r => [r.hit, r.miss, r.out]), usable.map(r => r.cost as number));
    out.solved_rates_usd_per_mtok = {
      cache_hit_input: +(h * 1e6).toFixed(4),
      cache_miss_input: +(m * 1e6).toFixed(4),
      output: +(o * 1e6).toFixed(4),
    };
    out.residuals = usable.map(r => ({
      tag: r.tag,
      actual: r.cost,
      predicted: +(r.hit * h + r.miss * m + r.out * o).toFixed(9),
    }));
    } catch (err) {
      // A rung that never returns a cache hit gives an all-zero column; record
      // that rather than losing the rows we paid for.
      out.solve_error = String(err);
    }
  } else {
    out.solve_error = `only ${usable.length} usable rows`;
  }

  writeFileSync(join(HERE, `solved_rates_${MODEL}.json`), JSON.stringify(out, null, 2));
  console.log(JSON.stringify({ rates: out.solved_rates_usd_per_mtok, residuals: out.residuals, err: out.solve_error }));
})();
