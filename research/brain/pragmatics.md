# Pragmatics & context interpretation — reference for the Messenger brain (wq#1361)

Campaign: brain-interp. Feeds wq#1353 (interpretation + classification). This is an
operational cheat sheet, not a lit review: every entry is `signal -> likely intent ->
how to respond / what to avoid`, meant to be applied inline during the reply step, not
studied in advance. Sources are named inline only where a term needs grounding; the
weight is on applicability.

Core frame (Grice, *Logic and Conversation*, 1975): people mean more than they say by
relying on shared assumption of cooperation. A reader who takes every message at face
value — pure semantic decoding, no inference — will misread a large fraction of real
messages, especially email and chat, which strip the channels (tone of voice, face,
timing) that carry disambiguating signal in speech. That gap is what this doc is for.

---

## 1. Implicature — meaning beyond the literal words

Grice's four maxims (quantity, quality, relation, manner) generate implicature when a
speaker appears to flout one but is still assumed cooperative. Practical version:

- **Signal:** reply gives *less* information than the question needs, or answers a
  different question than the one asked ("Did you finish the report?" -> "I started
  it yesterday"). **Likely intent:** the direct answer is unwelcome (no, and possibly
  not for a while) and the speaker is softening it. **Respond:** treat the indirect
  answer as the real answer (no) and ask the follow-up they're implying is needed
  (a timeline), don't just accept the non-answer at face value and move on.
- **Signal:** reply is *more* elaborate than the question needs — over-explaining,
  extra justification nobody asked for. **Likely intent:** anticipated pushback or
  guilt; the sender expects the answer to be unwelcome. **Respond:** the padding is
  itself information (they think this is a problem) — don't strip it out when
  summarizing, and don't respond only to the literal content.
- **Signal:** reply is a flat non-sequitur relative to the question ("Are you coming
  tonight?" -> "My sister's flight got in late"). **Likely intent:** the connection is
  meant to be inferred (no, because of the sister) — treat as a polite decline, not a
  broken conversation. **Avoid:** asking "what does that have to do with it," which
  reads as obtuse or accusatory; just infer the connective tissue.
- **Signal:** a qualifier that implies its own negation ("some of the team liked it").
  **Likely intent:** *not all* — often the rest didn't. Scalar implicature: "some"
  conversationally implies "not all" even though it's logically compatible with "all."
  **Respond:** don't round "some" up to "most/all" in a summary; the gap is the
  message.

## 2. Presupposition — what's assumed true before the sentence even starts

A presupposition survives negation and questioning ("Did you fix the bug *again*?"
presupposes a prior instance regardless of yes/no). Two failure modes:

- **Signal:** a question or statement bakes in a fact you haven't confirmed ("when
  you resend the invoice, also cc finance" presupposes an invoice send is already
  planned). **Likely intent:** the sender believes this is settled, possibly from an
  earlier thread you don't have full context on. **Respond:** if the presupposed
  fact is wrong or unconfirmed, surface it explicitly ("just to confirm — is a resend
  already planned?") rather than silently answering the surface request and inheriting
  a false premise.
- **Signal:** loaded phrasing ("now that the deploy's finally stable..."). **Likely
  intent:** a judgment is smuggled into the presupposition (it wasn't stable before,
  and that's implicitly someone's fault). **Avoid:** repeating the loaded frame back
  uncritically in a reply — it can read as agreeing with an accusation you didn't
  mean to endorse.

## 3. Speech acts — especially indirect requests

Direct speech act (imperative: "Send me the file") vs. indirect (declarative or
interrogative doing an imperative's job: "Do you have the file?" / "It would help to
have the file"). Searle's point: the literal form doesn't determine the function.

- **Signal:** "Could/Can/Would you [X]?" **Likely intent:** almost always a request,
  not a genuine capability question. **Respond:** do X (or explain why not); do not
  answer "yes I can" and stop. This is the single most common LLM misread trap —
  treating a conventionalized indirect request as literal Q&A.
- **Signal:** "Do you know if [X]?" / "I don't suppose you'd know...". **Likely
  intent:** a request for the information itself, hedged to make refusal easy.
  **Respond:** answer with the information if you have it; "yes I know" alone is a
  non-answer.
- **Signal:** a bare statement of a problem with no explicit ask ("the build's been
  failing since Tuesday," "I never got a reply to my last email"). **Likely intent:**
  request for action or acknowledgment, delivered as a complaint to preserve face —
  asking directly would expose the sender to a "no." **Respond:** treat as a request:
  fix it / acknowledge and give a timeline. Don't respond only with sympathy or
  factual confirmation ("yeah that sounds frustrating") and consider the message
  handled.
- **Signal:** a statement framed as information but placed where a decision is
  expected ("I noticed we're now the only team not on the new system"). **Likely
  intent:** indirect suggestion/request to migrate, using peer pressure as leverage
  rather than asking outright. **Respond:** address the underlying ask (should we
  migrate?), not just confirm the fact.
- **Failure mode to avoid:** over-literalizing formal/legal/scheduling language, where
  the *direct* form usually is the intended act ("This is a formal request for...",
  "Please confirm by EOD"). Directness there is a genre marker (see §4), not
  brusqueness — don't add a hedge that isn't in the source and don't read hostility
  into required-directness genres.

## 4. Register & formality — calibrate before you calibrate the reply

Register is the dial the sender used to signal relationship and stakes; matching it
(or deliberately not matching it) is itself part of the reply's content.

- **Signal:** full sentences, no contractions, formal salutation/sign-off, third-person
  or passive constructions ("It has been decided that..."). **Likely context:**
  distance, hierarchy, or a paper trail is being built (legal, HR, external). **Respond:**
  match formality; don't loosen it with jokes/emoji/casual sign-offs — that reads as not
  taking the situation seriously, even if the content is friendly.
- **Signal:** fragments, lowercase, minimal punctuation, in-group shorthand. **Likely
  context:** low stakes, existing rapport. **Respond:** matching casual register is
  usually safe; over-formalizing a reply to a casual message reads as cold or as
  status-signaling ("I am writing to confirm receipt of your message" to a "hey got
  a sec?").
- **Signal:** a sudden shift in register mid-thread (a normally casual sender goes
  formal, or vice versa). **Likely intent:** the register shift *is* the message —
  formalizing usually signals rising stakes, anger, or an intent to create a written
  record; loosening usually signals de-escalation or relief. **Respond:** treat the
  shift as more informative than the literal words of that message, and mirror the
  new register rather than the thread's historical baseline.
- **Trap:** don't infer *emotional temperature* from formality alone. Formal language
  can be neutral-professional, not upset — the tell is the shift, plus content (see
  §5), not formality in isolation.

## 5. Tone, sarcasm & irony in text — cues without voice or face

Text strips prosody (pitch, stress, timing) and facial/gestural cues that mark irony
in speech. Written irony has to be reconstructed from weaker, more ambiguous signals,
and false positives/negatives are both costly (misreading sincerity as sarcasm reads
as paranoid; missing sarcasm reads as obtuse).

- **Signal:** positive-valence words applied to an obviously bad outcome ("great, another
  outage," "love that for us," "cool cool cool" after bad news). **Likely intent:**
  sarcasm expressing frustration, not endorsement. **Respond:** address the underlying
  frustration/problem, never the literal positive sentiment — replying "glad you're
  happy!" to sarcasm is one of the most visible misread failures.
- **Signal:** excessive punctuation/caps used ironically against mundane content ("WOW
  what a SURPRISE the deploy broke again"). **Likely intent:** sarcastic emphasis, not
  genuine excitement. **Respond:** same as above — read the valence as negative.
- **Signal:** a hedge or scare-quote around a word ("the 'quick' fix," "he was
  'helping'"). **Likely intent:** the quoted word is being disputed or mocked, not
  used at face value. **Respond:** don't take the quoted claim as endorsed by the
  writer; it's usually the opposite.
- **Signal:** deadpan understatement of something large ("small issue: prod is down").
  **Likely intent:** the understatement is itself a stylistic marker of urgency/dry
  humor, not a minimization of severity. **Respond:** calibrate response to the actual
  content (prod down = urgent), not the flattened tone.
- **Genuine ambiguity — when you can't tell:** short, valence-neutral replies ("k",
  "fine", "sure") after a request or bad news are the highest-ambiguity case in text
  — could be sincere agreement or clipped irritation, and there's no reliable textual
  tell to disambiguate on a single message. **Respond:** don't guess a strong reading
  either way; if the stakes are low, take it at face value; if stakes are high (e.g.
  it gates an action), a brief neutral check-in beats confidently acting on a guessed
  emotional read. Do not manufacture certainty the text doesn't support.

## 6. Hedging — what qualifiers are doing

Hedges (*I think*, *maybe*, *sort of*, *just*, *kind of*, *not to be a pain but*) shield
face and manage commitment. They're rarely about genuine epistemic uncertainty.

- **Signal:** hedge + a request or opinion ("just wondering if maybe we could push the
  deadline"). **Likely intent:** full-strength request, softened to reduce the cost of
  a "no" for both sides. **Respond:** treat as the real ask at full weight; don't
  discount it proportionally to the hedging or answer the surface-level "wondering" as
  idle curiosity.
- **Signal:** hedge stacking (multiple hedges on one clause: "I could be totally wrong
  but maybe just possibly..."). **Likely intent:** the topic is higher-stakes or more
  face-threatening than the hedges suggest — heavy hedging correlates with sensitivity,
  not with actual low confidence. **Respond:** take the content seriously in proportion
  to the hedge density, inverted from the literal wording.
- **Signal:** "just" minimizing scope ("can you just take a quick look"). **Likely
  intent:** politeness minimizer, not a genuine claim the task is small. **Avoid:**
  taking "just"/"quick" as license to under-deliver or as evidence the task really is
  trivial — verify scope independently.
- **Trap — don't over-hedge in the other direction:** an LLM reply step that adds its
  own hedges reflexively ("I think maybe possibly...") on top of a confident source
  message reads as evasive. Match the sender's actual confidence, don't default to
  maximal hedging as a safety posture.

## 7. Politeness strategies — face-work (Brown & Levinson, *Politeness*, 1987)

Two kinds of "face": positive face (wanting to be liked/approved of) and negative face
(wanting autonomy, not to be imposed on). Politeness strategies exist to soften
face-threatening acts (requests, criticism, refusals).

- **Signal:** praise/agreement bundled before a request or criticism ("this is great
  work, one small thing though..."). **Likely intent:** positive-face redress — the
  praise is sincere-adjacent but partly there to cushion what follows. **Respond:**
  weight the "small thing" as the actual payload; don't let the praise crowd it out,
  but also don't discard the praise as pure filler — acknowledge both.
- **Signal:** off-record hints instead of direct criticism ("it's a bit warm in here"
  meaning "open the window"; "the deck's pretty long" meaning "cut it down"). **Likely
  intent:** deniable indirection — lets the speaker retract if the hint isn't taken.
  **Respond:** act on the implied request; if truly ambiguous, a direct check ("want
  me to shorten it?") is safe because it doesn't force the speaker to have "asked."
- **Signal:** negative-face redress before an imposition ("I know you're swamped,
  but...", "no worries if not, but..."). **Likely intent:** genuine request, with an
  explicit-looking escape hatch that is usually *not* meant to be taken as full
  permission to decline without comment. **Respond:** honor the escape hatch's spirit
  (a "no" is genuinely more acceptable here) but still give a real answer, don't
  silently drop the request because it offered an out.
- **Signal:** apology-heavy phrasing for a minor or routine ask ("so sorry to bother
  you, I feel bad asking, but..."). **Likely intent:** either genuine conflict-
  aversion or a signal the sender perceives the ask as a bigger imposition than it is
  (worth knowing for relationship calibration). **Avoid:** matching with equally heavy
  reassurance ("no bother at all!!") repeatedly — it can read as performative after
  the first instance; a brief warm response is enough.

## 8. Absence of tone-of-voice and face — why email/chat misreads happen more

Speech carries prosody, pauses, facial expression, and immediate repair (the listener
can ask "wait, what do you mean?" in real time). Text has none of that, and the
asynchrony adds its own distortion:

- **Flattening effect:** neutral text defaults to reading as more negative than
  intended (the "flat text = curt/annoyed" bias is well documented in CMC research).
  **Practical rule:** a short, unembellished reply to a substantive message is not
  reliable evidence of anger — don't infer hostility from brevity alone; look for
  actual negative-valence content or a register shift (§4) before concluding that.
- **No real-time repair:** ambiguity that would be resolved in one clarifying turn in
  speech instead sits unresolved for an entire message. **Practical rule:** when a
  message is genuinely ambiguous between two readings with different consequences,
  prefer a short clarifying question over picking the more "natural" reading —
  the cost of a wrong confident guess is higher in async text than in live speech,
  because there's no immediate correction loop.
- **Compressed cues carry outsized weight:** because so much signal is stripped, the
  cues that remain (punctuation, capitalization, response latency, message length,
  emoji or its absence) get overloaded with meaning by the reader. A late reply, a
  one-word reply, or a dropped emoji the sender usually uses can all be read as
  meaningful even when they're not. **Practical rule:** treat these as weak signals
  to combine with content, never as sufficient evidence alone for an emotional read.
- **No shared immediate context:** the sender may be replying with knowledge of an
  event, conversation, or deadline the reader doesn't have. **Practical rule:** when
  a message seems to presuppose context you don't have (§2) or reacts
  disproportionately to something that looks minor, the likelier explanation is
  missing context, not an overreaction — ask or flag the gap rather than assuming
  the visible message is the whole picture.

## 9. Common misread traps — summary checklist

Quick pre-send check for the reply step; each maps back to a section above.

1. Answered the literal question but ignored what the answer implied (§1).
2. Accepted a presupposed fact in the message without confirming it was true (§2).
3. Read an indirect request ("could you...", "do you know if...", a bare complaint)
   as literal information-seeking and gave only a yes/no or sympathy (§3).
4. Matched formality to the wrong baseline — over/under-formalized relative to the
   sender's own register, or missed a mid-thread register shift (§4).
5. Took sarcasm/irony at face value (or the reverse: read sincere text as sarcastic)
   (§5).
6. Discounted a hedged request as low-priority or as genuine uncertainty (§6).
7. Missed the payload behind praise-then-ask or an off-record hint, or ignored an
   explicit escape hatch on a softened request (§7).
8. Inferred anger/curtness from brevity or flat affect alone, with no corroborating
   content signal (§8).
9. Picked a confident reading of a genuinely ambiguous message instead of asking,
   when the two readings have materially different consequences (§5, §8).
10. Manufactured hedging/uncertainty in the reply that isn't warranted by the
    source message's actual confidence (§6).

---

## 10. Where this should inform the sanctioned-vs-protected classifier (wq#1353)

wq#1353 is the Messenger brain's interpretation+classification step; no
sanctioned/protected classifier code exists yet in the mailbot tree as of this
writing (`grep -rn -i "sanctioned\|protected" /home/lumen/mailbot/src/*.js` — no
hits), so this section is forward guidance for whoever builds it, not a citation of
existing logic:

- **Indirect requests (§3) must be recognized as requests before classification,
  not after.** If the classifier only sees literal speech-act form (imperative vs.
  question), it will systematically undercount requests phrased as questions or bare
  complaints — likely the single biggest source of missed "this needs action" cases.
  Run the pragmatic reframe (surface the implied act) upstream of whatever decides
  sanctioned vs. protected handling, so the classifier sees the *functional* act, not
  the grammatical form.
- **Register shifts (§4) and sarcasm markers (§5) are signal for escalation/priority,
  not just tone.** A sudden formalization or a sarcastic "great, another outage" both
  indicate rising stakes; if the classifier has any severity/urgency tier, these
  should nudge it up even when the literal words don't contain an urgent keyword.
- **Hedging density (§6) should not be read as low-confidence-therefore-low-priority.**
  If the classifier uses confidence language as a proxy for how seriously to treat a
  message, invert it: heavy hedging correlates with higher face-sensitivity, often
  higher real stakes, not lower.
- **Ambiguous cases (§5, §8 "genuine ambiguity") are exactly where sanctioned vs.
  protected gets risky to auto-decide.** Recommend the classifier route genuinely
  ambiguous tone/intent reads (no reliable textual tell, high-consequence action
  gated on the reading) to a clarify-first or human-escalation path rather than
  picking a confident label — this is the same principle as §8's "prefer a
  clarifying question when the cost of a wrong guess is high," applied to the
  classification decision itself rather than the reply content.
- **Don't let formality (§4) alone drive the sanctioned/protected boundary.** Formal
  register correlates with external/legal/HR contexts (plausibly "protected"-leaning)
  but is not sufficient alone — a formal message can be routine, and a casual message
  can carry a legally sensitive request. Use formality as one weighted signal among
  content-based ones, not a gate by itself.

---

*Primary framing sources (not exhaustively cited — this doc prioritizes applicable
heuristics over literature coverage): Grice, "Logic and Conversation" (1975) for
implicature/maxims; Searle, "Indirect Speech Acts" (1975) for the request-form
distinction in §3; Brown & Levinson, *Politeness: Some Universals in Language Usage*
(1987) for face-work in §7; general CMC (computer-mediated communication) research on
tone-flattening and irony markers in text for §5/§8.*
