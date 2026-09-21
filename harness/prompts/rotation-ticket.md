wq#2275 mask tripwire: mask-secrets pattern(s) {{TAG}} fired on wq#{{TICKET_ID}} attempt {{ATTEMPT}}'s stream-json. Artefacts on disk are masked, but the value reached the model context, so per instinct 4 it is burned.

FIRST run `python3 {{MASK_REPLAY}} {{TICKET_ID}}` (wq#2307): it replays that run's frozen corpus under the rules live when this fired, names every value by sha256 against the tracked tree, and reports how many are unaccounted. Zero unaccounted means committed text, not a credential — settle the row on that evidence, rotate nothing. Reading the artefacts by hand instead is what re-arms this alarm and files the next ticket (wq#2290 -> #2297 -> #2303).

This row is keyed by the burned VALUE, not by the run (wq#2341): every later run that printed the same value is appended to the notes as `also fired on wq#N`, with its own records path, instead of filing another pri-1 row. Replay those runs too — the leak path can differ per run — and rotate once, here.

The same evidence raw is in {{HITS_PATH}}: each record carries the kept prefix, the value's length, 12 hex of its sha256 (sha256 a candidate and compare; the value itself is nowhere on disk) and a masked context window showing the command or file it printed from. Rotate whatever is unaccounted today, and close that leak path.
