/**
 * lumen-mail-probe — sends one synthetic message to lumen@lumennode.uk every
 * 30 minutes so the guardian can prove the whole email path end to end:
 * Cloudflare Email Routing -> lumen-mailbot -> DeepSeek -> reply -> D1.
 * wq#1252.
 *
 * The subject and body both carry an HMAC token derived from the current
 * 30-minute window and a secret shared with the guardian. Two reasons:
 *  - the guardian can tell a real probe from anything else that lands in D1,
 *    so the allowlist the mailbot grants this sender cannot be borrowed by
 *    someone who merely knows the address;
 *  - the token is in the BODY as well as the subject because the D1 `messages`
 *    table stores body but not subject (mailbot/migrations/0001_init.sql).
 *
 * No Auto-Submitted or Precedence header is set, deliberately: those are
 * exactly what mailbot's automation suppression keys off, and a probe that
 * gets suppressed proves nothing.
 */
import { EmailMessage } from "cloudflare:email";

const WINDOW_S = 1800;

async function probeToken(secret, nowMs) {
  const bucket = Math.floor(nowMs / 1000 / WINDOW_S);
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sig = await crypto.subtle.sign(
    "HMAC", key, new TextEncoder().encode(String(bucket))
  );
  return [...new Uint8Array(sig)]
    .map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

function buildRaw({ from, to, subject, body, messageId }) {
  return [
    `From: Lumen Probe <${from}>`,
    `To: <${to}>`,
    `Subject: ${subject}`,
    `Message-ID: ${messageId}`,
    `Date: ${new Date().toUTCString()}`,
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="utf-8"',
    "",
    body,
  ].join("\r\n");
}

async function send(env, nowMs) {
  const secret = env.PROBE_HMAC_SECRET;
  if (!secret) throw new Error("PROBE_HMAC_SECRET is not set");
  const token = await probeToken(secret, nowMs);
  const from = env.PROBE_FROM || "probe@lumennode.uk";
  const to = env.PROBE_TO || "lumen@lumennode.uk";
  const subject = `[LPG-PROBE ${token}] liveness check`;
  const body =
    `Automated liveness probe for wq#1252. Token: ${token}\n` +
    `Nothing here needs a thoughtful answer — any reply proves the path works.\n`;
  const messageId = `<lpg-${token}-${nowMs}@lumennode.uk>`;
  await env.MAILER.send(
    new EmailMessage(from, to, buildRaw({ from, to, subject, body, messageId }))
  );
  return { token, messageId };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      send(env, Date.now()).then(
        (r) => console.log("probe sent", r.token),
        (e) => console.log("probe send failed:", e.message)
      )
    );
  },

  // Manual trigger for the runbook's "is the probe itself alive" step. Guarded
  // by the same secret so the endpoint cannot be used to spam the inbox.
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/send") return new Response("not found", { status: 404 });
    const given = request.headers.get("x-probe-secret") || "";
    const expected = env.PROBE_HMAC_SECRET || "";
    if (!expected || given !== expected) {
      return new Response("forbidden", { status: 403 });
    }
    try {
      const r = await send(env, Date.now());
      return Response.json({ ok: true, token: r.token });
    } catch (e) {
      return Response.json({ ok: false, error: e.message }, { status: 500 });
    }
  },
};

export { probeToken, buildRaw };
