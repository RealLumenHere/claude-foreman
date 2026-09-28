# wq#3048: the one value mask_replay.py 2848 reported BURNED? once the replay read
# masking._orphan_spans(), pinned as committed text. Not a credential, and it never
# fired: wq#2848's run predates the backstop. The run's glued probe printed its own
# masked result back as `cfut` + MASKED + `AKIA` + a Q-filled body, and the orphan
# joins the two halves. Named by hash, in-process.
# (sha, label, prefix, prefix + value). Split every six characters so reading this
# file fires no rule; mask_replay.account() closes the joined string.
# Pinned by test_mask_replay.py::test_wq2848_pins_are_their_hashes_and_read_silently.
WQ2848_VALUES = (
    ("388fe10062ec", "cloudflare", "cfut",
     "cfutAK" "IAQQQQ" "QQQ"),
)
