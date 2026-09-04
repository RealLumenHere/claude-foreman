# Pragmatics & Context Interpretation Reference

Campaign: brain-interp (feeds wq#1353 interpretation+classification, Messenger brain reply-step).

**What this is:** an operational lookup for reading what a sender MEANT, not what
they typed — for use at the reply-generation step, after the message is parsed but
before a reply is drafted. Not a literature review. Each entry: **signal → likely
intent → how to respond / what to avoid**. Grounded in Grice's Cooperative Principle
and implicature (Grice 1975), speech act theory (Austin 1962, Searle 1975), Brown &
Levinson politeness theory (1987), and register/genre analysis — cited where the
citation earns its place, not for decoration.

**Core premise:** text strips tone of voice, facial expression, and timing cues that
carry a large share of meaning in spoken exchange. The brain's default failure mode is
over-literal reading — treating the sentence as the whole message. Every heuristic
below exists to catch a case where the literal content and the intended content
diverge.

---

## 1. Implicature — what's meant but not said

Grice's Cooperative Principle: speakers assume the listener will infer more than
the literal words state, by assuming the speaker is being relevant, informative,
truthful, and clear. When a maxim is visibly *flouted*, that flouting is itself a
signal.

| Signal | Likely intent | Respond | Avoid |
|---|---|---|---|
| Reply answers a narrower question than asked, or answers a different one | Deflection — sender doesn't want to answer the literal question (declining, hedging on a commitment, or the real answer is "no") | Treat the non-answer as the answer; don't re-ask the literal question a second time expecting a different result | Don't take the dodge as an oversight and just repeat the original question |
| Response is technically true but conspicuously underinformative ("I was in the building" when asked "were you in the meeting?") | Sender is avoiding a direct claim they could be held to | Read as evasive; if it matters, ask the narrower question explicitly instead of accepting the broad one | Don't infer the broader claim ("so you were at the meeting") — that's exactly the inference they're avoiding |
| Irrelevant-seeming aside dropped into a reply ("Sure, I can look at that — by the way, is the Q3 budget still open?") | The aside is doing real work: either the main ask, or a soft check-in dependent on the answer | Address both; don't bury the aside as if it were filler | Don't treat it as small talk and answer only the first sentence |
| Over-precise or padded answer to a simple question | Sender is pre-empting a follow-up objection, or covering for something | Read the padding as anxiety/defensiveness, not new information — respond to the underlying concern | Don't quote the padding back as if it were the substantive content |

## 2. Presupposition — what's assumed, not asserted

A presupposition survives negation and question form: "Did you fix the bug *again*?"
presupposes a prior fix attempt regardless of yes/no. Presuppositions embedded in a
question are a common trap: answering the question at face value tacitly accepts the
presupposition.

| Signal | Likely intent | Respond | Avoid |
|---|---|---|---|
| "Why did X happen?" / "When will you finally do X?" | Presupposes X happened / X hasn't been done — may be false | If the presupposition is wrong, correct it before answering ("X hasn't happened — did you mean Y?") | Don't answer "why" and thereby concede the premise if it's factually wrong |
| "Can you send the *updated* file?" (no prior update exists) | Sender believes an update exists, or is testing/prompting one | Flag the mismatch rather than sending the unchanged file under a new name | Don't silently comply and manufacture a fake "update" to match the presupposition |
| Loaded question ("are you still ignoring this?") | Presupposes prior neglect; often adversarial or venting | Address the presupposition directly and calmly; don't get pulled into defending the frame | Don't answer the surface question ("no I'm not ignoring it") without naming the false premise |

## 3. Speech acts — especially indirect requests

Searle: an utterance's *illocutionary force* (what act it performs — request, promise,
warning, complaint) is frequently not what its grammatical form (question, statement)
suggests. Indirect speech acts are the majority of polite requests in English.

| Surface form | Literal reading | Actual force | Respond |
|---|---|---|---|
| "Do you know what time it is?" | Yes/no question about knowledge | Request: tell me the time | Give the time, not "yes" |
| "It's pretty cold in here." | Statement about temperature | Request: close the window / turn up heat | Offer to act, or ask if they'd like you to |
| "I was wondering if you'd had a chance to look at this." | Statement of past mental state | Request for status + gentle nudge (often after some delay) | Give the status; treat the softness as politeness, not indifference — a real deadline may be behind it |
| "Would it be possible to get this by Friday?" | Question about possibility | Request/near-deadline, "possible" is a hedge for face-saving, not genuine openness to "no" | Treat Friday as the actual ask; if it's not possible, say so plainly rather than answering only the literal possibility question |
| "Just checking in" / "just following up" | Statement of action | Often a second request in polite disguise — the first one didn't land, or a deadline is approaching | Prioritize the checked-in-on item; don't reply with only "thanks for checking!" and no substance |
| "No worries either way, just curious" (attached to a real ask) | Disclaims urgency | Frequently the opposite — face-saving cover in case the answer is unfavorable, not license to deprioritize | Answer as though it does matter; don't use the disclaimer as permission to drop it |
| Statement of a problem with no explicit ask ("the deploy's been failing since this morning") | Report | Often an implicit request for help/action, especially if directed at someone who can fix it | Offer to help or ask what's needed, don't just acknowledge the fact |

**Directness scales with power/distance, not urgency.** A blunt imperative from a
peer ("send me the file") is often *less* urgent than an elaborately hedged request
from someone lower-status or less familiar ("no rush at all, but any chance...") —
the hedging is social distance management, not a signal of low priority. Calibrate
urgency from context (deadlines named, repetition, channel) not from politeness level.

## 4. Register & formality

Register is a relationship signal, not just a style choice. A shift in register
mid-thread is more informative than the register itself.

| Signal | Likely intent | Respond | Avoid |
|---|---|---|---|
| Sudden formality increase (first names → "Dear Mx. Surname", casual → full sentences and salutations) | Escalation: sender is now speaking for the record, involving others, or signaling displeasure | Match seriousness; consider this thread may be forwarded/CC'd upward | Don't reply in the old casual register — it reads as not registering the shift |
| Sudden informality drop-in (terse, no greeting, one line) after a normally warmer contact | Stress, time pressure, or annoyance — not necessarily anger at the reader | Reply efficiently, don't force small talk back into it | Don't read curtness as hostility by default; also don't ignore it if it's a break from that sender's baseline |
| Register mismatched to relationship (very formal to a close colleague) | Possibly addressing a group/CC, possibly sarcastic distancing, possibly a template/mass message | Check the recipient list and thread context before assuming personal tone | Don't assume it's about you specifically without checking |
| All-lowercase, no punctuation, casual | Low-stakes, or deliberately signaling ease/informality (can still contain a real ask) | Mirror register somewhat, but don't downgrade content seriousness just because form is casual | Don't dismiss a real request/deadline because it arrived informally |

## 5. Hedging

Hedges (*I think*, *maybe*, *sort of*, *just*, *kind of*, *not to be a bother, but*,
*this might be a dumb question*) soften face-threat. They are not reliable signals of
the speaker's actual confidence or of the request's actual importance.

| Signal | Likely intent | Respond | Avoid |
|---|---|---|---|
| Hedged claim ("I think there might be an issue with...") | Speaker may be quite confident but softening to avoid overclaiming or blame; or genuinely unsure | Treat as a real flag worth checking, weight it by the speaker's track record — don't discount by the hedge alone | Don't wait for a stronger unhedged claim before acting; many careful people never drop the hedge even when certain |
| Self-deprecating preface ("this is probably a stupid question, but...") | Face-management before an ask that may seem obvious or reveal a gap | Answer the question plainly, don't validate or dwell on the disclaimer | Don't reassure at length ("not a stupid question at all!") — it reads as patronizing; just answer |
| "Just" minimizer ("can you just take a look") | Downplays the size of the ask, often unconsciously — the actual task may be nontrivial | Scope the real effort before agreeing; the "just" is not a size estimate | Don't take "just" as a promise it'll be quick |
| Multiple hedges stacked ("I could be wrong, but maybe possibly...") | High face-threat topic — often delivering bad news or a criticism | Prioritize this message; the stacking signals the speaker rehearsed it | Don't skim past it as low-confidence noise |

## 6. Politeness strategies (Brown & Levinson face theory)

Every request threatens the receiver's *negative face* (autonomy, not being imposed
on) and the requester's own *positive face* (wanting to be liked). Politeness
strategies manage that threat; the strategy chosen is itself informative.

| Strategy | Example | What it signals |
|---|---|---|
| Bald on-record (no softening) | "Send me the report." | High urgency, high familiarity, or a power differential where softening isn't expected — not necessarily rudeness |
| Positive politeness (in-group markers, compliments, "we") | "You're always so on top of this — could we get the report today?" | Relationship-building alongside the ask; the compliment is real social work, respond warmly but still deliver on the ask |
| Negative politeness (hedges, questions, apologies, minimizers) | "Sorry to bother you — would it be possible to get the report at some point today, if you have time?" | Respect for autonomy; common in formal/hierarchical or low-familiarity contexts. Do not read the softening as low priority |
| Off-record (hints, no explicit request) | "I haven't seen the report yet." | Maximum face-saving — gives the receiver an easy way to comply without ever having been "asked", or to ignore it without ever having "refused." Recognize the implicit request; a literal-only reader misses it entirely |

**Rule of thumb:** politeness strategy tracks *social risk management*, not *task
priority*. Don't infer urgency or importance from softening level; infer it from
content (deadlines, consequences named, repetition, escalation in register).

## 7. Tone, sarcasm, and irony cues in text

Sarcasm detection in plain text is unreliable without paralinguistic cues (pitch,
pause, facial expression) that carry it in speech; text substitutes weaker,
gameable proxies. Treat these as raised-hand signals, not proof.

| Cue | Reading | Caution |
|---|---|---|
| Exaggerated positive language about a clearly negative event ("Oh great, ANOTHER outage") | Likely sarcastic/frustrated, not genuinely positive | Don't take the words at face value and respond as if pleased |
| Punctuation/caps for emphasis ("sure, that'll work" vs "SURE, that'll work") | Emphasis marking often signals irony or frustration, but is also just emphasis in casual writers | Weight against the sender's normal baseline style — some people always write that way |
| Scare quotes around a word ("the 'urgent' request") | Distancing/skepticism from the quoted framing — signals disagreement with how something was characterized | Address the skepticism, don't just answer using their quoted term as if endorsed |
| Flat statement of an absurdly bad outcome with no hedging or apology | Possible dry/deadpan humor, or possible genuine severity — text alone often can't disambiguate | When ambiguous and stakes are real, ask a clarifying question rather than guessing; don't joke back if there's any chance it's genuine |
| Emoji/punctuation softening a sharp statement (🙃, "lol", "haha" after a complaint) | The laughter/emoji is doing face-saving work, not neutralizing the complaint | Treat the complaint as real; don't let the softener cancel it out |
| No emoji/punctuation softening at all on a thread that normally has it | Possible seriousness escalation or irritation | Notice the absence as a signal, especially against that sender's own baseline |

**When genuinely ambiguous and consequential, do not guess.** Ask a short, neutral
clarifying question ("want me to take that at face value, or is this more of a
vent?") rather than committing to a sarcastic or literal reading that could be wrong
in either direction.

## 8. Absence of tone-of-voice / face: how it changes email/text interpretation

Email and chat strip prosody (stress, pitch, pace) and facial/gestural cues that
disambiguate tone in speech. Research on email miscommunication (Kruger et al. 2005,
"Egocentrism over e-mail") found people systematically overestimate how well their
own intended tone comes through in text, and readers fill the gap with their own
mood/expectations rather than the sender's intent — a negativity-bias problem: a
terse or ambiguous message defaults toward being read as *more* negative than
intended, not less.

**Operational consequences for the reply step:**

- **Default away from negative readings of ambiguous brevity.** A short reply
  ("ok.", "noted", no exclamation where you'd expect one) is very often just
  brevity/time pressure, not coldness. Don't mirror perceived coldness back;
  reply at normal warmth unless other signals (register shift, explicit
  complaint) corroborate irritation.
- **Don't manufacture tone that isn't load-bearing.** Because the reader will
  fill gaps with their own inference, ambiguous phrasing in the brain's own
  *outgoing* replies is a liability — prefer slightly more explicit warmth/
  clarity than would be needed face-to-face, since there's no tone of voice to
  carry the intended meaning.
- **When stakes are real and the only signals available for a negative
  interpretation are only vaguely worded** ("this seems off"), consider asking
  one clarifying question rather than picking the darker interpretation.
- **Weight patterns over single messages.** One ambiguous message is noise; a
  register shift sustained across several messages, or an explicit statement of
  frustration, is signal. Don't overreact to a single terse line.

## 9. Common misread traps (checklist)

1. **Literal-answer trap** — answering the surface question when an indirect
   speech act (§3) was the real ask.
2. **Hedge-as-low-priority trap** (§5) — discounting a hedged or minimized
   request as unimportant.
3. **Politeness-as-priority trap** (§6) — reading elaborate softening as low
   urgency, or bluntness as anger, when both usually track social distance/
   power, not urgency.
4. **Negativity-default trap** (§8) — reading ambiguous brevity as coldness or
   anger by default, because text strips the tone that would normally rule
   that out.
5. **Sarcasm false-positive trap** (§7) — reading dry, deadpan, or just
   badly-phrased text as sarcastic and replying flippantly to something meant
   sincerely (higher-cost failure than the reverse in most operational
   contexts — a flip reply to a sincere, possibly distressed message lands
   worse than a sincere reply to a joke).
6. **Presupposition-acceptance trap** (§2) — answering a loaded or
   presupposition-laden question without flagging a false premise.
7. **Register-blindness trap** (§4) — missing an escalation signal (increased
   formality, going to writing "for the record") because the literal content
   of the message hasn't changed.
8. **Over-hedging the reply itself** — mirroring the sender's own uncertainty
   markers back at them so heavily that the actual answer gets buried; state
   the answer, then the caveat, not the reverse.

## 10. Worked examples

**Example A — indirect request + hedge stack**
> "Hey, no worries if not, but I was wondering if there's any chance you could
> take a look at the deploy script before EOD? Totally understand if you're
> slammed."

- Surface form: a low-stakes question with an explicit opt-out.
- Reading: heavy hedging (§5) + disclaimed urgency (§3) is very often cover for
  a real, time-boxed ask ("before EOD" is a real deadline, not decorative).
  The "no worries if not" is face-management, not permission to deprioritize.
- Response: treat this as a same-day request. If it can be done, confirm
  concretely ("on it, will have it to you by 4"). If not, say so plainly and
  give a real reason/ETA rather than leaning on the sender's own opt-out
  ("no worries" back) as an excuse to drop it.

**Example B — presupposition in a complaint**
> "Why does this keep breaking every time we deploy?"

- Surface form: a "why" question.
- Reading: presupposes (a) a pattern of recurring breakage and (b) that the
  reader knows the cause. If neither is actually true (this is the first
  time, or the reader doesn't know), answering "why" concedes both false
  premises.
- Response: "This is actually the first deploy-time break I've seen for this —
  can you point me at the others you're thinking of? Happy to dig into root
  cause." Corrects the premise before it hardens into an accepted narrative.

**Example C — terse message, ambiguous tone**
> "got it."

- Surface form: two words, no punctuation warmth, no context.
- Reading (§8): default away from a negative reading. Absent any other
  register shift, escalation, or explicit complaint in the thread, this is
  almost certainly just brevity — busy, on mobile, or simply not a person who
  pads acknowledgments.
- Response: reply at normal warmth/register; do not become guarded or
  apologetic in the next message as if something went wrong.

**Example D — sarcasm ambiguity, high stakes**
> "Cool, love that the prod database is 'read-only' now. Great timing."

- Surface form: positive words ("cool", "love that"), scare quotes on
  "read-only" (§7 — distancing/skepticism), "great timing" as likely irony
  given prod is presumably broken.
- Reading: near-certainly sarcastic frustration about an unplanned or
  badly-timed read-only lock, not genuine enthusiasm.
- Response: address the underlying problem directly ("checking now — will
  have it back to normal or an ETA shortly"), do not respond to the literal
  positive framing ("glad you're pleased!") and do not match sarcasm with
  sarcasm in an operational/support context.

---

## Relevance to the sanctioned-vs-protected classifier (wq#1353)

This reference should inform that classifier at the *signal extraction* stage,
before the sanctioned/protected decision is made, in three specific ways:

1. **Don't classify on literal content alone.** An indirect request (§3) or an
   off-record hint (§6) can carry a sanctioned action request without any
   imperative or keyword the classifier might pattern-match on. The classifier
   needs the illocutionary force (what act is being performed), not just the
   sentence type.
2. **Hedging and politeness level are not confidence signals for the
   classifier either.** A heavily hedged, elaborately polite message can
   still be requesting a fully sanctioned action; a blunt one can still be
   protected/refuse-worthy. Don't let register or politeness strategy shift
   the sanctioned/protected boundary — classify on the extracted intent, not
   the surface courtesy.
3. **Sarcasm and negativity-default (§7, §8) are classifier failure modes to
   guard against directly**: a sarcastic statement of a bad outcome
   ("great, love that you deleted prod") is not itself a request or
   instruction and should not be misclassified as one; conversely, a flatly
   worded but sincere request should not be downgraded in perceived severity
   just because it lacks emotional markers. When the interpretation layer
   flags ambiguity per §7/§8, the classifier should prefer the
   more-conservative (protected-leaning) branch rather than resolving the
   ambiguity itself — disambiguation belongs in interpretation, not
   classification.

## Sources

- Grice, H.P. (1975). "Logic and Conversation" — Cooperative Principle, maxims,
  conversational implicature.
- Austin, J.L. (1962). *How to Do Things with Words* — locutionary/illocutionary/
  perlocutionary acts.
- Searle, J.R. (1975). "Indirect Speech Acts" — indirect requests as the
  majority form of polite requests in English.
- Brown, P. & Levinson, S. (1987). *Politeness: Some Universals in Language
  Usage* — positive/negative face, politeness strategies.
- Kruger, J., Epley, N., Parker, J., & Ng, Z. (2005). "Egocentrism over
  e-mail: Can we communicate as well as we think?" *JPSP* — overestimation of
  tone transfer in text, negativity bias in ambiguous email.
