import os
import sys

import pytest

TESTS = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.dirname(TESTS)
sys.path[:0] = [TESTS, BUNDLE, os.path.join(BUNDLE, "..", "..", "harness")]   # masking.py

import selfheal_common as common  # noqa: E402
from selfheal_fakes import FakeOutlets, FakeSystemd, Proc  # noqa: E402


@pytest.fixture
def make_ctx():
    def _make(cfg=None, state=None, systemd=None, now=1_000_000.0, dry_run=False,
              runner=None, open_tickets=None):
        return common.Ctx(cfg or {}, {} if state is None else state,
                          systemd=systemd or FakeSystemd(),
                          outlets=FakeOutlets(open_tickets=open_tickets),
                          now=now, dry_run=dry_run,
                          runner=runner or (lambda argv, **kw: Proc()))
    return _make
