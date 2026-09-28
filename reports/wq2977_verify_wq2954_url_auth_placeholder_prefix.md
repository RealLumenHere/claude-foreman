# wq#2977 verify: wq#2954 (url-auth placeholder exclusion anchored to the `@`)

## Verdict
CONTRADICT, fixed in the same pass (claude-foreman fix/url-auth-placeholder-prefix).
The anchoring works. The prefix that may precede the placeholder is wider than the fix says it is.

## What holds
- The wq#2945 probe (`reports/wq2945_probe_url_auth_placeholder_glued_secret.py`) passes on main, exit 0.
- Pure placeholders (`<password>`, a token prefix then `<token>`, `tok_<body>`) stay excused.
- A live head glued to `<truncated>` or `<redacted-rest>` masks and alarms.

## What breaks
wq#2954's comment and its test docstring say the excused head is "at most a token prefix".
The regex is `(?:[a-z][a-z0-9]{0,5}[_-]){0,3}`: up to three FREE lowercase segments, each up to
six characters and closed by `_` or `-`. A live value shaped like that is excused whole:

    userinfo `abc123-def456_ghi789-` + `<x>` + `@host`  ->  no mask, no alarm, 21 live characters in the clear

The same input with four segments, or with an uppercase character, masks, which is why the
fix's own four `live` cases (`hunter2-...`, `...-`, a token prefix plus a live head, four
`_`-segments) all passed. None of them is three lowercase segments. This is the wq#2945
silent-leak class again, narrower: it needs a lowercase-and-digit head of at most three
segments, but it is the shape of a passphrase, not of a token.

## Fix
The excused head is the closed `_PRE_*` vocabulary this table already keeps (github, github_pat,
cloudflare, sk with its one provider qualifier, slack, aws) or ONE short lowercase segment.
One segment stays because `tok_<body>` is a pinned wq#2931 case. A single leftover head is at
most seven characters and can only precede a placeholder, so the exposure is bounded.

## Evidence
- New test `test_url_auth_placeholder_prefix_is_not_free_segments` (3 heads), all fail on main.
- New test `test_url_auth_pure_placeholder_behind_a_token_prefix_is_still_excused` (9 prefixes)
  guards against re-opening wq#2931.
- `harness/tests/test_masking.py`: 149 passed, 1 failed. The failure is
  `test_the_mask_suite_does_not_arm_its_own_tripwire`, red on main before this change; the files it names are
  committed reports under reports/ (wq3021, wq3062, wq3067, wq3070) that carry whole credential shapes, none of them touched here.
- Only `harness/masking.py` carries this exclusion; the daemon and the sed mirror have no placeholder screen and mask these inputs.

## Not done
The four reports the tripwire names belong to other tickets. They should be rewritten to describe shapes
rather than spell them, or a rotation ticket will file on the next read.
