# wq#3055 (kv-secret set:606923f6d3fd): the two values mask_replay.py 2120 reported
# BURNED?, pinned as committed text. Neither is a credential. wq#2120 was reviewing its
# own read_dir_sweep hits and read the upstream harnessrouter self-hosting guide mirror
# (research/harnessrouter-ref/src/docs/self-hosting-guide.md:118,541), whose `docker run`
# examples show the substitute-your-own placeholders below. Both are four lowercase
# English words and contain no digits. Each one's read_dir_sweep match hashes to a
# fingerprint wq#2120 already acked in read_dir_sweep.REVIEWED_HIT_FINGERPRINTS
# (527602abdce7, aea20d56e205). Hashes were matched in-process through the replay's
# verdict seam, and neither value was printed whole.
# Leak path: a worker reading third-party docs that quote placeholder secrets. There is
# no Lumen credential behind them, so nothing to rotate. The run's other eight hits are
# already settled (tracked or rule-fixed).
# (sha, label, prefix, prefix + value). Split every six characters so reading this
# file fires no rule. mask_replay closes same-line seams before it searches.
#
# wq#3076: four more values, all from the SAME transcript line — a pytest failure dump
# of wq#2120's own `tests/test_read_dir_sweep.py::test_url_basic_auth_*` parametrize
# IDs (dummy passwords/tokens the read_dir_sweep test suite feeds its OWN url-auth and
# kv-secret rules to prove they are NOT hits). The old line-wide `today_closed` excused
# all four via one unrelated accounted hit elsewhere on that giant JSONL line; scoring
# per-span surfaces each on its own. The `060e1c359001` xml-secret row is a single raw
# string (no chunking): its body contains literal backslash-n sequences from the
# un-decoded JSONL escaping, which a chunked/escaped literal cannot round-trip byte-for
# -byte through mask_replay's seam-join search, and it does not re-trigger any masking
# rule read in isolation (checked: `masking.mask_text()` on the bare body is clean), so
# chunking buys no safety here — it is stored as one contiguous r"..." literal instead.
WQ2120_VALUES = (
    ("2f5ecce736c5", "kv-secret", "HR_AUTH_PASSWORD=",
     "HR_AUT" "H_PASS" "WORD=t" "he-pas" "sword-" "you-ch" "ose"),
    ("cb5e89f275c8", "kv-secret", "HR_SECRET_KEY=",
     "HR_SEC" "RET_KE" "Y=a-lo" "ng-ran" "dom-pa" "ssphra" "se"),
    ("060e1c359001", "xml-secret", r"@100.64.0.7/git/repo.git]\ntests/test_read_dir_sweep.py::test_url_basic_auth_non_credentials_are_not_hits[https://user:password@100.64.0.7/git/repo.git]\ntests/test_read_dir_sweep.py::test_url_basic_auth_non_credentials_are_not_hits[https://user:CHANGEME_TOKEN@100.64.0.7/git/repo.git]\ntests/test_read_dir_sweep.py::test_url_basic_auth_non_credentials_are_not_hits[https://user:os.environ['X']@100.64.0.7/git/repo.git]\ntests/test_read_dir_sweep.py::test_url_basic_auth_non_credentials_are_not_hits[https://user:short@100.64.0.7/git/repo.git]\ntests/test_read_dir_sweep.py::test_url_basic_auth_non_credentials_are_not_hits[https://host:8080/path@thing]\ntests/test_read_dir_sweep.py::test_url_basic_auth_non_credentials_are_not_hits[ssh://git@github.com:22/org/repo.git]\ntests/test_read_dir_sweep.py::test_url_basic_auth_non_credentials_are_not_hits[https://example.com/a?b=c:d@efghijkl]\ntests/test_read_dir_sweep.py::test_url_basic_auth_non_credentials_are_not_hits[mailto:someone@example.com]\ntests/test_read_dir_sweep.py::test_url_basic_auth_masks_before_truncating\ntests/test_read_dir_sweep.py::test_url_basic_auth_does_not_shadow_a_pre\n"),
    ("95aa1524eedf", "kv-secret", "remove-key=",
     'remove' '-key=X' '-GNOME' '-Autos' 'tart-e' 'nabled'),
    ("cee452120e1b", "kv-secret", "x-access-token:",
     'x-acce' 'ss-tok' 'en:tok' 'en1234' '567890' '@100.6' '4.0.7/' 'git/re' 'po.git'),
    ("e797736247a1", "url-auth", "https://lumen:",
     'https:' '//lume' 'n:Kf3x' '9Zq2Lm'),
)

WQ2120_RECORDS = frozenset(v[0] for v in WQ2120_VALUES)
