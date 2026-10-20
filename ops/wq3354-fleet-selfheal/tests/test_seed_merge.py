# wq#4445: seed-vs-live drift. install.sh used to seed selfheal.yaml only when
# absent, so seed keys added after first deploy never went live and --verify
# stayed green. selfheal_merge.py --check must fail on that drift; the merge must
# add missing keys without ever touching an existing live value or comment.
import os

import pytest
import yaml

import selfheal_merge as sm

BUNDLE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED_SEED = os.path.join(BUNDLE, "selfheal.yaml")

SEED = """\
ticket_lane: build
nodes:
  enabled: true
  offline_after_s: 1800
  ignore:
    - thor
    - apis
  sleepers:
    - hathor
  notes:
    hathor: seed text for hathor
    sia: seed text for sia
newclass:
  enabled: false
"""

# live: operator edited hathor's note and offline_after_s, dropped sia/apis/newclass
LIVE = """\
ticket_lane: build
nodes:
  enabled: true
  offline_after_s: 900   # operator tuned this down
  ignore:
    - thor
  sleepers:
    - hathor
  notes:
    hathor: OPERATOR text for hathor
"""


@pytest.fixture
def pair(tmp_path):
    seed = tmp_path / "seed.yaml"
    live = tmp_path / "live.yaml"
    seed.write_text(SEED)
    live.write_text(LIVE)
    return str(seed), str(live)


def test_check_fails_on_drift(pair, capsys):
    seed, live = pair
    assert sm.main(["--check", "--seed", seed, "--live", live]) == 1
    out = capsys.readouterr().out
    assert "MISS seed key not live: nodes.notes.sia" in out
    assert "MISS seed key not live: nodes.ignore.apis" in out
    assert "MISS seed key not live: newclass" in out
    # operator VALUE drift is not key drift
    assert "offline_after_s" not in out
    assert "hathor" not in out


def test_check_green_when_live_has_all_keys(pair, capsys):
    seed, live = pair
    assert sm.main(["--check", "--seed", seed, "--live", seed]) == 0
    # live-only keys are the operator's: INFO, not MISS
    assert sm.main(["--check", "--seed", live, "--live", seed]) == 0
    assert "INFO live-only:" in capsys.readouterr().out


def test_shipped_seed_checks_clean_against_itself(capsys):
    assert sm.main(["--check", "--seed", SHIPPED_SEED, "--live", SHIPPED_SEED]) == 0


def test_merge_adds_missing_never_overwrites(pair, capsys):
    seed, live = pair
    assert sm.main(["--seed", seed, "--live", live]) == 0
    merged = yaml.safe_load(open(live))
    assert merged["nodes"]["notes"]["sia"] == "seed text for sia"
    assert merged["nodes"]["ignore"] == ["thor", "apis"]
    assert merged["newclass"] == {"enabled": False}
    # operator values and comments survive
    assert merged["nodes"]["notes"]["hathor"] == "OPERATOR text for hathor"
    assert merged["nodes"]["offline_after_s"] == 900
    assert "# operator tuned this down" in open(live).read()
    # merge is idempotent and now checks clean
    assert sm.main(["--check", "--seed", seed, "--live", live]) == 0


def test_merge_backs_up_first(pair, tmp_path):
    seed, live = pair
    assert sm.main(["--seed", seed, "--live", live]) == 0
    baks = [p for p in os.listdir(tmp_path) if p.startswith("live.yaml.bak.")]
    assert len(baks) == 1
    assert (tmp_path / baks[0]).read_text() == LIVE


def test_dry_run_writes_nothing(pair):
    seed, live = pair
    assert sm.main(["--dry-run", "--seed", seed, "--live", live]) == 0
    assert open(live).read() == LIVE


def test_invalid_live_is_exit_2_and_untouched(pair):
    seed, live = pair
    with open(live, "w") as f:
        f.write("nodes: [unclosed\n")
    assert sm.main(["--check", "--seed", seed, "--live", live]) == 2
    assert sm.main(["--seed", seed, "--live", live]) == 2
    assert open(live).read() == "nodes: [unclosed\n"


def test_null_list_live_gets_members(pair, tmp_path):
    seed, _ = pair
    live = tmp_path / "null_list.yaml"
    live.write_text("nodes:\n  ignore:\n  sleepers: [hathor]\n")
    assert sm.main(["--seed", seed, "--live", str(live)]) == 0
    merged = yaml.safe_load(open(live))
    assert merged["nodes"]["ignore"] == ["thor", "apis"]
