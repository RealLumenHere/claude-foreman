# wq#3065 (kv-secret set:b05f5da514d1): the two values `mask_replay.py 3059` reported,
# pinned as committed text. Neither is a credential; nothing here was rotated.
#
# Both were cut out of wq#3059's OWN prose. wq#3059 was the verify-pass of wq#2120's
# read_dir_sweep ack list, and its notes describe the three hits that list acks:
#   - the upstream harnessrouter self-hosting doc mirror
#     (research/harnessrouter-ref/src/docs/self-hosting-guide.md:118 and :541), where the
#     sentence names the two HR_* env labels and then the ordinary English plural noun
#     below — the rule glued label and noun into one span (mask-hits-1.json prefix
#     "HR_SECRET_KEY= ", trailing space and all);
#   - research_prompts/.scratch/autorandr_Makefile:71, a `desktop-file-edit` flag whose
#     value is the freedesktop autostart key name below.
# Each preimage was confirmed by hash, not by printing a value: sha256(...)[:12] of the
# bare noun is f80e633770c8 and of the key name is 95aa1524eedf, the two shas the replay
# reports — a 48-bit preimage match on a string guessed from the hit's own context, which
# is what identifies these values. read_dir_sweep separately acks the Makefile hit as
# reviewed non-secret (ba3880ebd497, read_dir_sweep.py:180-182), but that fingerprint is
# over the span ITS regex cut from line 71, and research_prompts/.scratch is outside every
# dir a foreman worktree may read (the access gap wq#3059 itself recorded), so the span
# cannot be recomputed here and the pin does not lean on it.
#
# Leak path: a verify worker quoting, in English, which labels an upstream doc and a
# scratch Makefile carry. No Lumen credential exists behind either string, so there is
# nothing to rotate. wq#3061 reached the same disposition on false evidence — it cited
# dispatcher/DESIGN.md:462 ("lane cap defaults ... are placeholders scaled to"), an
# unrelated coincidence that `account()` accepted because the noun is exactly
# TRUNCATED_FLOOR long (wq#3062's CONTRADICT, ISSUE-LOG row 1012). These pins replace
# that coincidence with the real source.
#
# (sha, label, prefix, prefix + value). Split every six characters so reading this file
# fires no rule. mask_replay closes same-line seams before it searches.
WQ3059_VALUES = (
    ("f80e633770c8", "kv-secret", "HR_SECRET_KEY= ",
     "HR_SEC" "RET_KE" "Y= pla" "cehold" "ers"),
    ("95aa1524eedf", "kv-secret", "remove-key=",
     "remov" "e-key=" "X-GNOM" "E-Auto" "start-" "enabl" "ed"),
)

WQ3059_RECORDS = frozenset(v[0] for v in WQ3059_VALUES)
