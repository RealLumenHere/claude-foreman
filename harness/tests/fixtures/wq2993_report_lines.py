# wq#2993: pinned probe inputs for the mask-report exemption in read_dir_sweep.
# A mask finding row is a report ABOUT a credential; read_dir_sweep was flagging the
# mask tool's own output (119 of foreman's 390 hits were one mask_at_rest JSONL).
# Committed here so the probes replay against fixed bytes, not a live mutable log
# (wq#2630: an unpinned probe is not re-runnable evidence). Values are assembled at
# runtime, seams split, so no source literal in this file is itself credential-shaped.


def _row(label, context):
    return ('{"label": "' + label + '", "prefix": "http://lumen:", "value_len": 40, '
            '"value_sha256_12": "e78e97e6a223", "context": "' + context + '"}')


# The mask-log population: the value half is the fleet's own mask literal glued to a
# kept prefix. After wq#2993 this must not page anywhere — the literal is refused by
# the value half of the patterns themselves, not only inside report paths.
MASKED_URL = "http://lumen:" + "ghp_" + "MASK" + "ED@100.121.17.5:3000"
MASKED_KV = "signing secret = " + "0123456789abcdef" + "MASK" + "ED"
MASKED_GHPAT = "ghp_" + "0123456789abcdef" + "MASK" + "ED"
REPORT_ROW_MASKED = _row("url-auth", MASKED_URL.replace("http://", "thttp://"))

# The falsifiability probe, from OUTSIDE the mask-log population: the same JSONL
# `label: value` position, but a REAL-shaped body (no mask literal). This must still
# page when it sits outside a report path — if it goes quiet, the exemption or the
# literal refusal has grown past the population it was cut for.
REAL_PW = "Zq" + "8Lm2Kf3x9Wv7" + "Tr5"
REAL_URL = "http://lumen:" + REAL_PW + "@100.121.17.5:3000"
REPORT_ROW_REAL = _row("url-auth", REAL_URL)
