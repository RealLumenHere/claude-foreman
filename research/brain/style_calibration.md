# Per-Contact Style Calibration

Campaign: brain-interp. Ticket: wq#1363.

## Problem

Lumen's mailbot currently generates replies against one implicit style: a single
persona template (`buildSystem()` in `mailbot/src/worker.js`), plus whatever
owner-supplied lore a contact file injects (`mailbot/contacts/*.md`, loaded by
`loadContactLore()`, landed in wq#1359 PR#6). Neither varies the *shape* of a
reply — length, formality, directness, medium conventions — by who is on the
other end. That is wrong on its face: people convey and expect context
differently, and a fixed output style is legible to one kind of sender and
noise to another. This document is operational guidance for building and
maintaining a **per-contact style profile** inside the existing `contact_notes`
D1 table (wq#1167) and for using that profile when drafting a reply.

Scope: guidance and a schema proposal, not an implementation. No mailbot code
is changed by this ticket.

## Existing infrastructure this builds on

- `contact_notes` (D1, wq#1167): one row per contact email, with a `summary`
  column the worker loads into the prompt as "ABOUT THEM" and updates
  post-send via `ctx.waitUntil`. Columns `boundary_reminders` /
  `honesty_reminders` are reserved for wq#1170's unrelated consent-tracking
  feature — style calibration needs its own column, not those.
- `contacts/*.md` (owner-supplied, wq#1200/wq#1359): hand-written persona
  lore, loaded whole into the system prompt. This is Frank's voice about the
  contact, not an inferred model — it should stay authoritative and never be
  silently overwritten by the automated style profile below.
- `messages` table: has `direction`, `ts`, `body` per turn, joined through
  `threads` on `contact_email` (`loadHistory()`, `worker.js:306`). This is the
  raw material the style profile is inferred from — it already exists, so
  calibration is a read-and-summarize pass over data Lumen already stores, not
  new instrumentation.

## Dimensions of variation to track

These are the axes where senders differ enough that a single fixed style
misreads them. Each is a spectrum, not a category — store a position, not a
label.

1. **High- vs low-context (Hall).** Low-context senders spell out the ask,
   the deadline, the reason, explicitly, every time. High-context senders
   assume shared background and expect the reader to infer intent from what's
   *not* said — restating something they already told you reads as either
   distrust or condescension to them. Signal: ratio of explicit
   who/what/when/why statements to bare requests; whether they repeat context
   Lumen already has, or drop it and expect recall.

2. **Direct vs indirect.** Direct senders say "no" and "I need X by Friday."
   Indirect senders hedge ("maybe if you get a chance," "no worries either
   way") even when the ask is real and the deadline is firm. Reading an
   indirect sender's hedge as optional, or a direct sender's bluntness as
   anger, is the two failure modes this axis exists to prevent. Signal:
   density of hedges (maybe, if possible, no pressure, whenever), density of
   imperatives and firm deadlines, whether declines are stated or implied by
   topic change.

3. **Medium/generational norms.** Not an age guess — a norm inferred from
   behavior, because guessing generation from a name or writing sample is
   exactly the stereotyping failure mode in the section below. What to
   observe instead: message length trend, salutation/sign-off presence
   (full "Hi Lumen, ... Best, X" vs none), reply latency and whether they
   reply inline/top-post/quote, punctuation density, and whether long or
   short replies from Lumen get shorter or longer responses back — that last
   one is a direct behavioral signal of what medium convention *they* expect,
   independent of any demographic assumption.

4. **Verbosity/brevity as signal, not noise.** A contact who is normally
   terse sending three paragraphs is signal (something's wrong, or it's
   unusually important to them) exactly because it deviates from their
   baseline. A profile without a baseline can't detect the deviation. This is
   the strongest reason a per-contact model beats a global one: "long
   message" is meaningless without knowing what long means *for this
   contact*.

5. **Same words, different meanings by sender (idiolect).** "Sounds good" is
   enthusiastic agreement from one contact and a placeholder while they think
   of an objection from another. "lol" carries no humor from some senders and
   genuine amusement from others. These can't be resolved from the words
   alone — they require a per-contact history of what that phrase preceded
   last time (did "sounds good" get followed by a change of plan two threads
   later?). Store observed phrase → outcome pairs when they recur, not a
   sentiment score.

## What the profile stores

Extend `contact_notes` with a `style_profile` JSON column (proposed;
implementation ticket, not this one) rather than new scalar columns, because
the shape will need to grow (a new axis is a new key, not a migration):

```json
{
  "verbosity_baseline_words": 42,
  "directness": -0.3,
  "context_style": "high",
  "formality": "low",
  "sign_off": "none",
  "hedge_density": 0.18,
  "sample_size": 14,
  "last_updated": "2026-09-04T10:00:00Z",
  "phrase_notes": [
    {"phrase": "sounds good", "observed_outcome": "changed plan next thread, 2x"}
  ],
  "confidence": "low"
}
```

`directness` and similar continuous fields are cheap running estimates
(exponential moving average, see below), not model outputs — no LLM judge
call is needed per message; a small deterministic feature extraction (hedge
word list, message length, imperative detection) is enough and keeps the
update cheap enough to run in the existing `ctx.waitUntil` post-send hook
already used for the `summary` column.

## Updating the model over time

1. **Update on every inbound message, in the same post-send `ctx.waitUntil`
   hook that already updates `summary`.** No separate batch job — the
   infrastructure to touch `contact_notes` per-turn already exists (wq#1167).

2. **Recency-weighted, not overwrite-on-every-message.** Use an exponential
   moving average (e.g. `new = 0.8 * old + 0.2 * observed`) for continuous
   fields so one unusual message doesn't reset the baseline, but the profile
   still tracks genuine drift over weeks. A flat overwrite is too noisy; a
   frozen first-impression average is too slow to notice a relationship
   changing.

3. **Gate on `sample_size` before trusting the profile.** Below ~5 messages,
   mark `confidence: "low"` and fall back to the default persona style rather
   than calibrating off a single data point — a contact's first, terse "ok"
   message is not evidence they're always terse. This is the same shape as
   `loadRecentDodgeStyles()`'s use of a bounded recent window (`worker.js:317`)
   applied to confidence rather than variety.

4. **Treat a style shift as a signal, not just a data point.** A normally
   verbose, hedging contact suddenly sending one blunt line is worth surfacing
   in `summary` ("X's tone shifted noticeably in the last message") the same
   way the existing summary already accumulates sender-stated facts — style
   drift is itself often the most important content of a message.

5. **Never let the automated profile override `contacts/*.md`.** Owner-
   supplied lore is a standing instruction from Frank; the inferred
   `style_profile` is a description of behavior. If they conflict (lore says
   "always reply formally to X," observed style is casual), the lore wins and
   the conflict itself is worth a `summary` note, not a silent override.

## Using the profile when drafting a reply

- Match length to `verbosity_baseline_words`, not a fixed target — a 200-word
  reply to a contact whose baseline is one line reads as ignoring the register
  they set.
- Match `context_style`: for a high-context contact, don't restate
  background they already share; for a low-context contact, make the ask,
  deadline, and reason explicit even if it was implied earlier in the thread.
- Match `directness` cautiously in the direct→indirect direction (be
  respectfully direct back to a direct contact) but do not mirror indirection
  into evasiveness — Lumen still needs to actually answer the question; it
  can wrap the same content more gently for an indirect contact without
  becoming vague.
- Never mirror hostility. A terse, cold message from a contact whose baseline
  is warm is a signal to attend to (per point 4 above), not a style to match.

## Anti-patterns

- **Stereotyping from demographic guesses.** Inferring "older, so formal
  email style" or "younger, so texting style" from a name, email domain, or
  writing sample is exactly the failure this document is trying to prevent —
  it substitutes a cultural/generational assumption for observed behavior.
  Every field in `style_profile` must be derived from that specific contact's
  message history, never from a category guess about them.
- **Overfitting to one message.** See sample-size gating above.
- **Treating style as sentiment.** A terse or blunt style is not the same
  signal as anger; a contact's terse baseline should not be read as coldness
  every time they use it. The baseline exists precisely so deviations, not
  the baseline itself, carry emotional signal.
- **Letting the profile leak into the prompt as raw judgment.** "This contact
  is low-context and impatient" read verbatim by the model can bias tone
  unhelpfully; prefer feeding the concrete calibration knobs (target length,
  explicitness) over a characterization of the person.

## Worked example

`mailbot/contacts/jonathan.md` is hand-authored lore (direct, informal,
teasing register, explicitly instructed). A `style_profile` for Jonathan
inferred from his actual messages should never contradict that lore; it
should only fill in what the lore doesn't specify — e.g. his actual average
message length and whether he replies inline or top-posts — so a reply
matches his real rhythm on top of the persona Frank already wrote.
