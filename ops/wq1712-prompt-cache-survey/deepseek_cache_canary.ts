// wq#1712 — does DeepSeek's automatic context cache fire on real Lumen traffic?
//
// FLEET_COST_PLAN.md D5 says `cache_hit` is null on every paid SpendLogs row and
// concludes "no prompt caching is active anywhere". But LiteLLM's `cache_hit`
// column records LiteLLM's OWN response cache, not a provider-side prefix cache,
// so that observation cannot distinguish "DeepSeek is not caching" from "DeepSeek
// is caching and we never recorded it". deepseek-v4-flash is 60% of fleet spend;
// the difference is worth measuring directly.
//
// Method mirrors wq#1592's glm canary (same production prompt assembly from
// pokemon/src/prompt.ts) with two changes its own conclusion asked for:
//   - history padded to CFG.historyCompactAt (80) lines, not historyKeepVerbatim
//     (30), so the prompt lands near LPP's measured 6,575-token average instead
//     of the 2,004 tokens that canary tested. DeepSeek's cache has a documented
//     minimum-block granularity, so prompt size is a live variable.
//   - a 4th call that APPENDS one history line, which is what LPP actually does
//     every turn. If the provider caches on prefix, that call should still hit on
//     everything before the appended line. A pure byte-identical repeat is the
//     easy case; append-then-repeat is the case that decides the $/mo number.
//
// Read-only measurement. Changes no routing, writes no config.
//
// Run via lumen-run (needs the LPP-scoped gateway key at
// /home/lumen/.secrets/litellm-service-keys/lpp.key, which a worker sandbox
// cannot read — instinct 3 / guard.sh):
//   /home/lumen/bin/lumen-run start --name wq1712-dscanary -- \
//     /home/lumen/pokemon/node_modules/.bin/tsx \
//     /home/lumen/worktrees/wq1712-a1/ops/wq1712-prompt-cache-survey/deepseek_cache_canary.ts
//
// Writes results (no secrets) to deepseek_cache_canary.json next to this file.

import { writeFileSync, readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { SYSTEM_PREFIX, History, buildMessages, renderState } from "/home/lumen/pokemon/src/prompt.ts";
import { CFG } from "/home/lumen/pokemon/src/config.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const MODEL = process.env.WQ1712_MODEL ?? "deepseek-v4-flash";

function apiKey(): string {
  if (process.env.LITELLM_MASTER_KEY) return process.env.LITELLM_MASTER_KEY;
  return readFileSync("/home/lumen/.secrets/litellm-service-keys/lpp.key", "utf8").trim();
}

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

const acts = [
  "goto(6,9)", "interact(5,8)", "talk()", "leave_edge(N)", "goto(4,3)",
  "enter_warp(2)", "talk()", "choose(1)", "use_item(potion,1)", "battle_move(0)",
];
function histLine(t: number): string {
  const act = acts[t % acts.length];
  const result = `moved to (${(t * 3) % 20},${(t * 2) % 18}) on map${10 + (t % 3)}, no new events, party healthy`;
  return `T${t} [map${10 + (t % 3)} ${(t * 3) % 20},${(t * 2) % 18}] ${act} => ${result}`.slice(0, 190);
}

// wq#1592 measured 2,004 prompt tokens at 30 history lines; each line is ~32
// tokens, so ~175 lines is what puts this canary at LPP's measured 6,575-token
// average. That is more verbatim lines than CFG.historyCompactAt (80) allows in
// production, but a real 6.5k-token turn reaches that size through the folded
// summary + screen text + image content this synthetic prompt has no way to
// reproduce. Size is the variable under test here, not turn count.
const TURNS = Number(process.env.WQ1712_TURNS ?? 175);
const hist = new History();
for (let t = 1; t <= TURNS; t++) hist.push(histLine(t));

const notepad = "PIN: Brock beaten, badge 1\nExploring Route 3 for wild encounters, low on poke_balls\nNext: reach Pewter Mart for supplies";
const stateBlock = renderState(stateForRender, mapAscii) + "\nSCREEN TEXT: (none)";

const baseMessages = buildMessages(hist, notepad, stateBlock, "", "");

// One appended turn — the real per-turn delta LPP sends.
const histPlusOne = new History();
for (let t = 1; t <= TURNS + 1; t++) histPlusOne.push(histLine(t));
const appendedMessages = buildMessages(histPlusOne, notepad, stateBlock, "", "");

async function callRung(tag: string, messages: unknown) {
  const body = {
    model: MODEL,
    messages,
    max_tokens: CFG.profiles.primary.maxTokensOut,
    temperature: 0.6,
  };
  const t0 = Date.now();
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
    latency_ms: Date.now() - t0,
    prompt_tokens: usage.prompt_tokens ?? null,
    completion_tokens: usage.completion_tokens ?? null,
    // DeepSeek's native field names, plus the OpenAI-shaped one LiteLLM maps them into.
    prompt_cache_hit_tokens: usage.prompt_cache_hit_tokens ?? null,
    prompt_cache_miss_tokens: usage.prompt_cache_miss_tokens ?? null,
    cached_tokens: usage.prompt_tokens_details?.cached_tokens ?? null,
    usage_raw: usage,
    error: d.error ?? null,
  };
}

const sleep = (ms: number) => new Promise(res => setTimeout(res, ms));

(async () => {
  const calls = [];
  calls.push(await callRung("call1_cold", baseMessages));
  await sleep(2000);
  calls.push(await callRung("call2_identical_2s", baseMessages));
  await sleep(13000);
  calls.push(await callRung("call3_identical_15s", baseMessages));
  await sleep(2000);
  calls.push(await callRung("call4_appended_one_turn", appendedMessages));

  const out = {
    ts: new Date().toISOString(),
    model: MODEL,
    gateway: CFG.gateway,
    history_lines: TURNS,
    system_prefix_chars: SYSTEM_PREFIX.length,
    prompt_char_len: JSON.stringify(baseMessages).length,
    appended_prompt_char_len: JSON.stringify(appendedMessages).length,
    calls,
  };
  writeFileSync(join(HERE, `cache_canary_${MODEL}.json`), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out.calls.map(c => ({
    tag: c.tag, status: c.status, prompt: c.prompt_tokens,
    hit: c.prompt_cache_hit_tokens, miss: c.prompt_cache_miss_tokens, cached: c.cached_tokens,
  }))));
})();
