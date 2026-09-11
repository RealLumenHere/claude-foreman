// wq#1712 — is the gateway's recorded cost cache-aware?
//
// spend_accounting_probe.py wanted this from `/model/info` + `/spend/logs` and
// got 401 on both (the master-key file at /home/lumen/.secrets/litellm-master.env
// no longer authenticates — see the README). The same fact is reachable without
// admin scope: LiteLLM returns its computed cost for the request in the
// `x-litellm-response-cost` response header, and that is the number that lands in
// LiteLLM_SpendLogs.spend.
//
// So: fire the same prompt twice. Call 1 is a cache miss, call 2 is a ~98% cache
// hit (deepseek_cache_canary.ts already proved the hit happens). If both headers
// carry the same cost per prompt token, the gateway prices cache reads at the
// full input rate and every recorded DeepSeek dollar is an over-estimate.
//
// The prompt deliberately differs from the canary's (different notepad text) so
// call 1 is a genuine cold miss rather than a hit on the canary's own cache entry.
//
// Run via lumen-run (needs the LPP-scoped gateway key):
//   /home/lumen/bin/lumen-run start --name wq1712-costhdr -- \
//     /home/lumen/pokemon/node_modules/.bin/tsx \
//     /home/lumen/worktrees/wq1712-a1/ops/wq1712-prompt-cache-survey/cost_header_probe.ts

import { writeFileSync, readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { History, buildMessages, renderState } from "/home/lumen/pokemon/src/prompt.ts";
import { CFG } from "/home/lumen/pokemon/src/config.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const MODEL = process.env.WQ1712_MODEL ?? "deepseek-v4-flash";
const TURNS = Number(process.env.WQ1712_TURNS ?? 175);
const NONCE = process.env.WQ1712_NONCE ?? String(Date.now());

function apiKey(): string {
  if (process.env.LITELLM_MASTER_KEY) return process.env.LITELLM_MASTER_KEY;
  return readFileSync("/home/lumen/.secrets/litellm-service-keys/lpp.key", "utf8").trim();
}

const stateForRender = {
  map: 12, x: 6, y: 9, badgeCount: 1, money: 1340, inBattle: 0,
  party: [
    { species: 1, level: 14, hp: 38, maxHp: 42, status: 0, moves: [1, 45, 33, 0], pp: [30, 20, 20, null] },
    { species: 7, level: 11, hp: 29, maxHp: 29, status: 0, moves: [33, 55, 0, 0], pp: [30, 15, null, null] },
  ],
} as any;
const mapAscii = Array.from({ length: 9 }, (_, r) =>
  Array.from({ length: 9 }, (_, c) => (r === 4 && c === 4 ? "P" : (r + c) % 5 === 0 ? "G" : (r + c) % 7 === 0 ? "#" : ".")).join("")
).join("\n");

const acts = ["goto(6,9)", "interact(5,8)", "talk()", "leave_edge(N)", "goto(4,3)",
  "enter_warp(2)", "talk()", "choose(1)", "use_item(potion,1)", "battle_move(0)"];
const hist = new History();
for (let t = 1; t <= TURNS; t++) {
  hist.push(`T${t} [map${10 + (t % 3)} ${(t * 3) % 20},${(t * 2) % 18}] ${acts[t % acts.length]} => moved to (${(t * 3) % 20},${(t * 2) % 18}) on map${10 + (t % 3)}, no new events, party healthy`.slice(0, 190));
}
const notepad = `PIN: run ${NONCE}\nExploring Route 3 for wild encounters, low on poke_balls\nNext: reach Pewter Mart for supplies`;
const messages = buildMessages(hist, notepad, renderState(stateForRender, mapAscii) + "\nSCREEN TEXT: (none)", "", "");

async function call(tag: string) {
  const r = await fetch(CFG.gateway, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${apiKey()}` },
    body: JSON.stringify({ model: MODEL, messages, max_tokens: CFG.profiles.primary.maxTokensOut, temperature: 0.6 }),
  });
  const d: any = await r.json().catch(() => ({}));
  const usage = d.usage ?? {};
  return {
    tag,
    status: r.status,
    response_cost_usd: r.headers.get("x-litellm-response-cost"),
    prompt_tokens: usage.prompt_tokens ?? null,
    completion_tokens: usage.completion_tokens ?? null,
    prompt_cache_hit_tokens: usage.prompt_cache_hit_tokens ?? null,
    prompt_cache_miss_tokens: usage.prompt_cache_miss_tokens ?? null,
    error: d.error ?? null,
  };
}

const sleep = (ms: number) => new Promise(res => setTimeout(res, ms));

(async () => {
  const cold = await call("cold_miss");
  await sleep(3000);
  const warm = await call("warm_hit");
  const out = { ts: new Date().toISOString(), model: MODEL, nonce: NONCE, calls: [cold, warm] };
  writeFileSync(join(HERE, `cost_header_${MODEL}.json`), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out));
})();
