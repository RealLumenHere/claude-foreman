"""Static checks for the wq#3879 FwAnalyzer policies.

FwAnalyzer itself is not built on Lumen (go build is off the worker allowlist),
so these tests pin what can be checked without it: the TOML parses, each
FileContent rule has exactly one method (filecontent.go:58-67), includes
resolve, and every regex fires on the bad line and stays quiet on the good one.
The patterns use no lookarounds or backreferences, so Python re and Go RE2
agree on them.
"""

import re
import tomllib
from pathlib import Path

import pytest

HERE = Path(__file__).parent
POLICIES = sorted(HERE.glob("*.toml"))
METHODS = ("RegEx", "Digest", "Script", "Json")

# rule name -> (lines that must match, lines that must not)
CASES = {
    "passwd_empty_password": (
        ["guest::1000:1000::/home/guest:/bin/sh", "root::0:0:root:/root:/bin/ash"],
        ["root:x:0:0:root:/root:/bin/ash", "#guest::1000:1000::/:/bin/sh"],
    ),
    "passwd_uid0_accounts": (
        ["root:x:0:0:root:/root:/bin/ash", "admin:$1$ab$cd:0:0::/:/bin/sh"],
        ["nobody:x:65534:65534::/:/bin/false", "daemon:x:1:1::/:/bin/false"],
    ),
    "inittab_console_shell": (
        ["ttyS0::respawn:/bin/sh", "::askfirst:-/bin/ash", "console::respawn:-/bin/bash"],
        ["ttyS0::respawn:/sbin/getty -L ttyS0 115200 vt100", "#ttyS0::respawn:/bin/sh"],
    ),
    "rcs_starts_telnetd": (
        ["telnetd -l /bin/sh &", "  /usr/sbin/telnetd"],
        ["# telnetd disabled", "dropbear -s"],
    ),
}


def load(path):
    with path.open("rb") as f:
        return tomllib.load(f)


def file_content_rules():
    for path in POLICIES:
        for name, rule in load(path).get("FileContent", {}).items():
            yield name, rule


def test_policies_present():
    assert {p.name for p in POLICIES} == {"limpet_base.toml", "busybox_init.toml"}


@pytest.mark.parametrize("path", POLICIES, ids=lambda p: p.name)
def test_includes_resolve(path):
    for inc in load(path).get("Include", {}):
        assert (HERE / inc).is_file(), f"{path.name} includes missing {inc}"


@pytest.mark.parametrize("name,rule", list(file_content_rules()))
def test_exactly_one_method(name, rule):
    assert sum(m in rule for m in METHODS) == 1, name


def test_every_regex_rule_has_cases():
    regex_rules = {n for n, r in file_content_rules() if "RegEx" in r}
    assert regex_rules == set(CASES)


@pytest.mark.parametrize("name,rule", [(n, r) for n, r in file_content_rules() if "RegEx" in r])
def test_regex_cases(name, rule):
    assert rule.get("RegExLineByLine") and rule.get("Match"), name
    reg = re.compile(rule["RegEx"])
    hits, misses = CASES[name]
    for line in hits:
        assert reg.search(line), f"{name} missed {line!r}"
    for line in misses:
        assert not reg.search(line), f"{name} fired on {line!r}"


def test_os_release_extract():
    rule = load(HERE / "limpet_base.toml")["DataExtract"]["os_release"]
    body = 'NAME="OpenWrt"\nPRETTY_NAME="OpenWrt 23.05.3"\nID=openwrt\n'
    assert re.findall(rule["RegEx"], body) == ["OpenWrt 23.05.3"]
