# TEXT_AMBIGUITY — recovering intended meaning from imperfect text

CAMPAIGN brain-interp · wq#1362. Real email/chat is typed fast, on phones, under
autocorrect, by people with different dialects and different editing standards.
The literal string is not the message — the message is what the sender meant to
convey. This doc is an operational guide for closing that gap without guessing wrong.

## 0. The governing rule: fail to kindness

> **When an imperfect text has both a benign reading and a hostile/insulting
> reading, and the "error" plausibly explains the difference, take the benign
> reading. If the two readings are both plausible AND materially different in
> what they ask of you, do not silently pick — hedge in your reply or ask a
> short clarifying question. Never silently assume the reading that makes the
> sender rude, incompetent, or hostile.**

This is the fail-to-kindness default applied to parsing, not just to tone: the
failure mode to design against is an interpreter that is *technically* parsing
correctly (or picking the syntactically-primary reading) but landing on an
uncharitable meaning the sender never intended. A dropped "not," a missing
comma, or a fat-fingered autocorrect swap should never be the reason Lumen
reads a person as an asshole. Getting this wrong is worse than asking — a
clarifying question costs one round-trip; a wrong hostile read costs the
relationship.

Two failure directions, both bad, but not symmetric in cost:
- **Under-correcting** (parsing the literal, broken string): "no thanks
  needed" read as refusing gratitude when it meant "no, thanks needed" is rare
  and usually low-stakes.
- **Over-correcting** (silently rewriting what someone actually rule-governedly
  said into what you expect standard English to say): erasing a dialect
  feature, a real negation, or a real boundary because it "looked like" a typo.
  This is worse because it discards signal the sender put there on purpose.

The heuristics below exist to tell these apart.

## 1. Error categories and how each shifts meaning

### 1.1 Missing or misplaced punctuation
Comma placement changes grammatical role (address vs. object, restrictive vs.
non-restrictive), and its absence is the single highest-leverage error class
because casual typing drops commas far more than it drops words.

- **The "comma of life"**: "Let's eat, Grandma" (invitation to Grandma) vs.
  "Let's eat Grandma" (cannibalism). The missing comma turns a vocative
  address into a direct object. Applies to any sentence with a name/title
  right after a verb: "Thanks Bob I appreciate it" — is Bob being thanked, or
  is "Bob" incidental and the thanks aimed elsewhere? Almost always the
  vocative reading is intended; casual typing drops the comma near-universally.
- **Restrictive vs. non-restrictive clauses**: "my brother who lives in Reno
  is visiting" (implies multiple brothers, this is the Reno one) vs. "my
  brother, who lives in Reno, is visiting" (one brother, incidentally in
  Reno). Casual writers rarely mark this distinction either way — infer from
  what you know about the sender's family, not from comma presence/absence.
- **Missing terminal punctuation on a short reply changes tone, not content**:
  "sure." vs "sure" vs "sure!" — the period-only reply reads as curt/annoyed
  in chat registers (the "period of doom") but in email or from senders who
  reliably close every sentence with a period, it's just their punctuation
  habit and carries no extra weight. Calibrate against the sender's own
  baseline (§3), not against chat-app norms, unless you know the channel.

### 1.2 Dropped or garbled negation
The single highest-consequence error class: dropping "not," "n't," or "un-"
inverts truth value instead of just shifting emphasis.

- Autocorrect and fast typing both drop "n't" ("I cant make it" parses fine
  either way; "I dont think thats a good idea" — the "dont" is doing real
  work, don't lose it).
- Watch for **haplography at word boundaries**: "I would not not recommend
  this" typed as "I would not recommend this" by accidental de-duplication,
  or the reverse (auto-suggest inserting a stray "not"). Rare, but check when
  a sentence's stance contradicts everything else in the message — that
  contradiction is itself the signal something got dropped.
- **"could care less"**: idiomatically means "couldn't care less" (total
  indifference), not literal partial caring. This is semantic bleaching /
  idiom fossilization, not a grammar error — treat it as the fixed phrase it
  is, never as a logic error to "correct" in interpretation. (Leading
  theories: sarcastic elision, or blending with "as if I could care less.")
  Same family: "I'm not unconvinced" ~ "I'm somewhat convinced" (litotes, not
  cancellation to neutral).
- **Double negatives as agreement, not cancellation**: "I don't have no time"
  / "I ain't got no money" — in AAVE and many English dialects, negative
  concord means the negation is *reinforced*, not cancelled, unlike formal
  logic where two negatives make a positive. Reading "I don't have no time" as
  "I have time" is a parsing error, not a charitable one — it inverts meaning
  by applying a register the sender isn't using. See §2 on dialect.

### 1.3 Homophones and near-homophones (autocorrect/fat-finger artifacts)
their/there/they're, your/you're, its/it's, then/than, to/too/two, whose/who's,
lose/loose. These are near-universally **typos, not meaning signals** — the
substituted word is phonetically identical or adjacent, so treat them as noise
to normalize silently unless the "wrong" word actually produces a more
coherent reading than the "right" one in context (rare, but check: sometimes
what looks like a typo is the intended word and the "corrected" reading is
the one that doesn't fit).

- Autocorrect-specific swaps to recognize: "definitely" → "defiantly",
  "of" → "off", "no" → "on"/"not", "in" → "im"/"i'm" collisions, a wrong word
  that is a keyboard-adjacent or predictive-text near neighbor of an
  in-context word. If a sentence has exactly one word that breaks its
  coherence and that word has a common autocorrect neighbor that would fix
  it, prefer the fix.
- Do not over-apply this: a homophone swap that still produces a coherent,
  differently-meaning sentence ("your welcome" for "you're welcome" changes
  nothing meaningful either way) is safe to silently normalize. One that
  would flip an instruction ("their coming with us" vs a genuinely intended
  "there, coming with us" — contrived, but the test is: does normalizing
  change what action you'd take? If not, don't bother flagging it at all.)

### 1.4 Run-ons and missing scope boundaries
No punctuation between clauses forces you to infer where one thought ends and
the next begins — get the boundary wrong and you attach a modifier, a
negation, or a conditional to the wrong clause.

- "call me when you get this isn't urgent" — is "isn't urgent" describing the
  call, or is this two run-together thoughts ("call me when you get this."
  "[it] isn't urgent.")? The run-on erased the sentence boundary. Prefer the
  segmentation that makes both halves independently sensible statements the
  sender would plausibly make back to back.
- Long unpunctuated emails: segment by locating verbs and treating each
  finite verb as anchoring roughly one clause; conjunctions without commas
  ("and," "but," "so") are still reliable boundary markers even when commas
  are missing.

### 1.5 Ambiguous modifier attachment
Classic PP-attachment and scope ambiguity, worse under casual typing because
there's no comma to disambiguate.

- "I saw the guy with the binoculars" — who has the binoculars, the seer or
  the guy? Resolve via world knowledge/context (what's plausible given the
  rest of the thread), not via syntax alone — syntax is genuinely ambiguous
  here.
- **"only" placement**: "I only said that to Jim" (I said it just to Jim, no
  one else) vs. "I said only that to Jim" (that's all I said) vs. "I said that
  only to Jim" (same as first). Casual writers put "only" near the verb
  regardless of true scope — don't read narrow scope just because "only" sits
  next to the verb; infer intended scope from what makes sense as the actual
  claim being made.
- **Squinting modifiers**: "people who complain often are ignored" — does
  "often" modify "complain" or "are ignored"? If either reading changes your
  response, this is a §4 flag-it case, not a guess-it case.

### 1.6 Sarcasm and tone without markers
Plain text strips prosody, so sarcasm often looks identical to sincerity in
the literal string.

- Weak signals worth weighting: excessive punctuation ("great!!!" after bad
  news), all-caps on an ordinary word, "yeah, sure," "no worries" immediately
  after describing something that clearly is a worry, a positive statement
  that contradicts the email's established stakes.
- Absent an explicit marker (/s, heavy irony cues, or contradiction with
  established context), do not assume sarcasm — assuming sarcasm on a sincere
  message is itself an uncharitable misread (it implies the sender is being
  passive-aggressive when they were just being nice). Sincerity is the prior;
  sarcasm needs positive evidence, not just "this seems like a weird thing to
  say earnestly." When genuinely torn, hedge (§4) rather than commit.

**Sarcasm punctuation** is its own inversion pattern worth naming, since it's
punctuation doing the inverting rather than wording:
- A flat, deliberately under-punctuated period on a statement that would
  normally get an exclamation point — "Great." after describing a setback —
  reads as deflated/sarcastic in chat register, the inverse of what "Great!"
  would signal. This is the same "period of doom" flatness as §1.1's
  terminal-punctuation note, but here the flatness inverts the word's
  sentiment rather than just reading as curt.
- Scare quotes around a single word ("thanks for the 'help'") invert that
  word's sentiment specifically, leaving the rest of the sentence sincere —
  don't over-read tone into the whole message, only the quoted word.
- A trailing ellipsis on an agreement ("sure...") signals reluctance or doubt,
  not plain assent — treat it as a softer, hedged "sure," not a full "sure!".
- These markers are still weak signals per the base rate above (sincerity is
  the prior): a period habit or an ellipsis habit that's consistent for a
  given sender (§2) is idiolect, not sarcasm, and should not be re-read as
  tone on every message from them.

## 2. Dialect / idiolect is not error

Before "correcting" any of the above, ask: **is this consistent for this
writer, or a one-off slip?** A pattern the sender uses every time (negative
concord, habitual "be," dropped copula, regional lexical choices, non-native
article/preposition patterns, texting-register lowercase-and-no-punctuation as
their permanent style) is a **feature of their idiolect**, not an error to
normalize away. Rule-governed dialects are systematic, not random:

- AAVE negative concord ("I don't have no time"), habitual "be" ("she be
  working nights" = regularly, not "she is working nights" right now),
  invariant "be" and copula absence all follow consistent internal rules —
  treat their meaning at face value within that system, not as broken
  Standard English.
- Non-native speakers often show consistent article/preposition/tense
  patterns shaped by their first language. These are not typos and won't
  resolve to a "typo fix" — parse for the intended content, don't flag them
  as errors needing correction.
- Texting/email register conventions (no capitalization, no terminal
  punctuation, "u"/"ur," minimal commas) are a register choice for an entire
  population of senders, not a personal error — don't read tone-negativity
  into a lowercase, unpunctuated message from someone who always writes that
  way.

**The test that separates dialect from a genuine slip**: does this sender
produce the same construction consistently across messages/time, or does it
contradict their own established pattern in this one instance? Consistency →
dialect/idiolect, treat as intended. A one-off construction that breaks their
own established pattern (someone who is normally careful and precise suddenly
drops a "not") → more likely a genuine slip, weigh autocorrect/haplography
explanations from §1.2 more heavily.

## 3. Practical decision procedure

1. **Read for the most charitable, internally coherent reading first.** Does
   a benign interpretation exist that accounts for the "error" as typo,
   autocorrect, punctuation drop, or dialect? If yes and it's the only
   coherent reading, use it — done, no flag needed.
2. **Check the sender's own baseline** (their prior messages, if available).
   Does this construction match how they always write? If so, it's not an
   error at all (§2) — parse literally within their idiolect.
3. **If multiple readings are coherent, weigh by register and base rate**:
   in fast casual text, "dropped punctuation/apostrophe" is far more probable
   than "sender meant something structurally unusual." Default to the
   boring, common explanation (typo) over the exotic one (deliberate
   ambiguity, hidden meaning).
4. **Check consequence, not just plausibility.** Two readings can both be
   plausible but only one matters: if reading A and reading B lead to the
   same action on your part, don't bother resolving further — pick either
   and move on. If they diverge in what you'd do next (confirm vs. cancel,
   agree vs. refuse, apologize vs. not), continue to step 5.
5. **When a real fork remains** (plausible + coherent + consequential, and
   step 2's baseline doesn't settle it): don't silently guess. Either (a)
   hedge — respond in a way that's correct under both readings, or (b) ask a
   short, specific clarifying question naming the actual fork ("just
   confirming — did you mean send it today, or hold off?"). Never let the
   hedge or question itself imply the sender was unclear in a careless way;
   frame it as normal confirmation, not correction.
6. **Never let an uncharitable reading win a tiebreak.** If step 5's fork
   includes one reading where the sender is being rude/dismissive/hostile and
   another where they're neutral/friendly and the "error" plausibly explains
   the gap, the neutral/friendly reading wins the tiebreak outright — this
   isn't a coin flip, it's the fail-to-kindness default from §0 overriding
   ties.

## 4. Flag vs. silently normalize — quick reference

| Situation | Action |
|---|---|
| Homophone typo, no meaning change either way | Silently normalize, no mention |
| Comma-of-life / vocative ambiguity with an obvious sane reading | Silently take the sane reading |
| Dialect feature consistent with sender's history | Parse literally within that dialect, don't flag |
| Dropped negation that would invert a low-stakes statement | Silently take the non-inverted (charitable) reading |
| Dropped negation that would invert a high-stakes commitment (money, deadline, consent) | Hedge or ask — too costly to guess even toward kindness |
| Modifier/scope ambiguity where both readings lead to the same action | Silently pick either, move on |
| Modifier/scope ambiguity where readings diverge in required action | Ask, naming the specific fork |
| Literal string reads as hostile, but an error explains it and a benign reading is coherent | Take the benign reading, never the hostile one |
| Literal string reads as hostile, no plausible error explains it, sender's baseline shows no such pattern | Take it at face value — fail-to-kindness is a *default under ambiguity*, not a license to disbelieve real anger |

The last row matters: fail-to-kindness is about not manufacturing hostility
out of noise, not about refusing to recognize hostility that's actually
there. If someone is clearly upset and says so plainly, don't "hedge" that
away — the rule only fires when an error plausibly created the appearance of
hostility that the sender didn't intend.
