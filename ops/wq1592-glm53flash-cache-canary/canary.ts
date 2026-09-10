// wq#1592 — D5 canary (FLEET_COST_PLAN.md order-of-work step 3).
//
// Builds LPP's REAL prompt shape via the production prompt.ts (SYSTEM_PREFIX,
// History, buildMessages, renderState — not a hand-rolled approximation) and
// fires it at glm-5.3-flash over the OpenRouter route on the live gateway,
// twice back-to-back with a byte-identical prefix, to measure whether the
// route actually returns prompt_tokens_details.cached_tokens on a second hit
// and how many completion_tokens the mandatory reasoning burns. Nothing here
// changes routing — this is read-only measurement, run once, output logged.
//
// Run via lumen-run (needs the LPP-scoped key at
// /home/lumen/.secrets/litellm-service-keys/lpp.key, which this worker
// session's own sandbox cannot read — instinct 3 / guard.sh):
//   /home/lumen/bin/lumen-run start --name wq1592-canary -- \
//     /home/lumen/pokemon/node_modules/.bin/tsx \
//     /home/lumen/worktrees/wq1592-a1/ops/wq1592-glm53flash-cache-canary/canary.ts
//
// Writes results (no secrets) to canary_results.json next to this file.

import { writeFileSync, readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { SYSTEM_PREFIX, History, buildMessages, renderState } from "/home/lumen/pokemon/src/prompt.ts";
import { CFG } from "/home/lumen/pokemon/src/config.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

function apiKey(): string {
  if (process.env.LITELLM_MASTER_KEY) return process.env.LITELLM_MASTER_KEY;
  return readFileSync("/home/lumen/.secrets/litellm-service-keys/lpp.key", "utf8").trim();
}

// Synthetic but shape-real: same PartyMon/GameState fields agent.ts feeds
// renderState, and the same "T<n> [map<m> x,y] act => result" line format
// agent.ts:1508 pushes into History every turn.
const party = [
  { species: 1, level: 14, hp: 38, maxHp: 42, status: 0, moves: [1, 45, 33, 0], pp: [30, 20, 20, null] },
  { species: 7, level: 11, hp: 29, maxHp: 29, status: 0, moves: [33, 55, 0, 0], pp: [30, 15, null, null] },
];
const stateForRender = {
  map: 12, x: 6, y: 9, badgeCount: 1, money: 1340, inBattle: 0,
  party: party.map(p => ({ species: p.species, level: p.level, hp: p.hp, maxHp: p.maxHp, status: p.status, moves: p.moves, pp: p.pp })),
} as any;

const mapAscii = Array.from({ length: 9 }, (_, r) =>
  Array.from({ length: 9 }, (_, c) => (r === 4 && c === 4 ? "P" : (r + c) % 5 === 0 ? "G" : (r + c) % 7 === 0 ? "#" : ".")).join("")
).join("\n");

const hist = new History();
const acts = [
  "goto(6,9)", "interact(5,8)", "talk()", "leave_edge(N)", "goto(4,3)",
  "enter_warp(2)", "talk()", "choose(1)", "use_item(potion,1)", "battle_move(0)",
];
for (let t = 1; t <= CFG.historyKeepVerbatim; t++) {
  const act = acts[t % acts.length];
  const result = `moved to (${(t * 3) % 20},${(t * 2) % 18}) on map${10 + (t % 3)}, no new events, party healthy`;
  hist.push(`T${t} [map${10 + (t % 3)} ${(t * 3) % 20},${(t * 2) % 18}] ${act} => ${result}`.slice(0, 190));
}

const notepad = "PIN: Brock beaten, badge 1\nExploring Route 3 for wild encounters, low on poke_balls\nNext: reach Pewter Mart for supplies";
const stateBlock = renderState(stateForRender, mapAscii) + "\nSCREEN TEXT: (none)";

const messages = buildMessages(hist, notepad, stateBlock, "", "");

async function callRung(tag: string) {
  const body = {
    model: "glm-5.3-flash",
    messages,
    max_tokens: CFG.profiles.primary.maxTokensOut,
    temperature: 0.6,
    reasoning_effort: CFG.profiles.primary.reasoningEffort,
  };
  const r = await fetch(CFG.gateway, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${apiKey()}` },
    body: JSON.stringify(body),
  });
  const status = r.status;
  const d: any = await r.json().catch(() => ({}));
  const usage = d.usage ?? {};
  return {
    tag, status,
    prompt_tokens: usage.prompt_tokens ?? null,
    completion_tokens: usage.completion_tokens ?? null,
    cached_tokens: usage.prompt_tokens_details?.cached_tokens ?? null,
    cache_write_tokens: usage.cache_write_tokens ?? null,
    reasoning_tokens: usage.completion_tokens_details?.reasoning_tokens ?? null,
    usage_raw: usage,
    error: d.error ?? null,
  };
}

const sleep = (ms: number) => new Promise(res => setTimeout(res, ms));

(async () => {
  const promptCharLen = JSON.stringify(messages).length;
  const call1 = await callRung("call1_cold");
  await sleep(2000);
  const call2 = await callRung("call2_repeat_prefix_2s");
  await sleep(8000);
  const call3 = await callRung("call3_repeat_prefix_10s");
  const out = {
    ts: new Date().toISOString(),
    model: "glm-5.3-flash",
    route: "openrouter",
    prompt_char_len: promptCharLen,
    history_lines: CFG.historyKeepVerbatim,
    calls: [call1, call2, call3],
  };
  writeFileSync(join(HERE, "canary_results.json"), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out));
})();
